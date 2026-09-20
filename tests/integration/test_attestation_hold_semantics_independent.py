"""HOLD-state semantics — independent scenarios for the LCA attest-first
contract (merge-gate evidence, 2026-09-20).

Scopes four corner cases of the 2026-09-19 attest-first HOLD-state
contract (:data:`daemon.graph.ATTESTATION_REMINDER_CAP`,
:data:`daemon.graph.ATTESTATION_REMINDER_COUNT_KEY`,
``Decision.HOLD`` arm of ``decide()``/``evaluate()`` + the
``create_attestation_gate_node`` factory):

1. REMINDER CAP (third-HOLD behavior) — three consecutive attest-only
   turn-ends: 1st → HOLD+reminder(count→1), 2nd → HOLD+reminder(count→2),
   3rd → plain ``meta_bypass`` allow + reminder count CLEARED to 0. The
   cap is the documented escape so a leader stuck emitting empty / short
   finals is NEVER trapped in an infinite HOLD loop.

2. COUNTER STATIC AT BOUND — prime ``attestation_denied_count=2``
   (``deny_bound=3``) via prior DENIED turns, then 2 HOLD cycles →
   counter STAYS 2 (never incremented, never consulted, no
   terminal_after_bound); then a proper attested allow → counter
   RESETS to 0 (the attested-allow reset trigger 1).

3. NO-ATTESTATION PRECEDENCE — the FIFTH legitimate-pending input
   ``user_answer_pending=True`` precedes the attested/HOLD step
   (clean-allow BEFORE any trigger/judge/reminder work); the branch-5/R2
   legitimate-pending input ``pending_children>0`` is the canonical
   precedence of the R2 non-reset semantics over the deny/HOLD machinery.

4. WINDOW INTEGRITY — attest turn → HOLD+reminder → report turn (NO
   new ``attest_completion`` call) → attestation STILL in-window
   (window=3; the original attest call sits 2–3 messages back including
   the reminder HumanMessage) → ALLOWED at the report turn-end (NOT a
   fresh deny).

Harness mirrors ``test_attestation_user_answer_pending_lca.py`` —
stub manager + minimal in-memory ledger, REAL
``create_attestation_gate_node`` factory closure, REAL
``evaluate()``/``decide()`` path. NO real LLM calls.

Contract reference: ``.agents/shared/planning/leader-completion-
attestation/decisions.md`` lines 1864–1940 (D-entry 2026-09-19 —
incident c5d9a38a remediation).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
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
    GateSettings,
    build_gate_config,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — minimal harness
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _FakeLedger:
    """Minimal in-memory ledger that mimics the duck-typed
    :class:`AttestationLedger` interface the gate node consumes.

    Tracks ``increment`` / ``reset`` / ``set_escalated_and_reset`` calls
    (the same Protocol surface ``safe_increment`` / ``safe_reset`` /
    ``safe_set_escalated_and_reset`` route through). The current
    ``denied_count`` is the only piece of mutable state the gate's
    ``denied_count_getter`` reads back — we expose it via the ``state``
    dict + the explicit ``current_denied_count`` getter.
    """

    denied_count: int = 0
    increment_calls: list[tuple[str, str]] = field(default_factory=list)
    reset_calls: list[str] = field(default_factory=list)
    escalated_calls: list[str] = field(default_factory=list)

    def increment(self, instance_id: str, denial_epoch: str) -> int:
        self.denied_count += 1
        self.increment_calls.append((instance_id, denial_epoch))
        return self.denied_count

    def reset(self, instance_id: str) -> bool:
        self.denied_count = 0
        self.reset_calls.append(instance_id)
        return True

    def set_escalated_and_reset(self, instance_id: str) -> bool:
        self.denied_count = 0
        self.escalated_calls.append(instance_id)
        return True

    def get(self, instance_id: str) -> int:
        return self.denied_count


def _make_manager(
    *,
    pending_children: int = 0,
    queued_wakeups: int = 0,
    live_descendants: int = 0,
    busy_descendants: int = 0,
    answer_pending: bool = False,
) -> MagicMock:
    """Build a MagicMock manager exposing the FOUR R2 facades +
    ``has_open_user_answer`` (the FIFTH legitimate-pending input,
    FIX-2 incident 6a0d60c9).

    Defaults are zero/false (the "no legitimate pending" baseline) so
    the gate's deny/HOLD branches stay reachable unless a test
    explicitly arms one of the inputs.
    """
    m = MagicMock()
    m.count_pending_children.return_value = pending_children
    m.get_queued_or_expected_wakeups.return_value = queued_wakeups
    m.count_live_descendants.return_value = live_descendants
    m.count_busy_descendants.return_value = busy_descendants
    # ``has_open_user_answer`` MUST return the literal ``True`` — the
    # gate's duck-typing guard reads ``_answer_reader(instance_id) is
    # True`` (NOT just truthy) so a MagicMock auto-attr without a
    # configured return_value would NOT arm the bypass (see
    # ``tests/integration/test_attestation_user_answer_pending_lca.py::
    # test_truthy_non_bool_facade_value_never_arms_the_bypass``).
    m.has_open_user_answer.return_value = answer_pending
    return m


def _make_node(
    *,
    instance_id: str,
    ledger: _FakeLedger,
    manager: MagicMock,
    denied_count: int,
    deny_bound: int = 3,
    window: int = 3,
) -> Any:
    """Build the REAL ``create_attestation_gate_node`` factory closure.

    Args:
        instance_id: Thread-id under test.
        ledger: :class:`_FakeLedger` instance (the ``safe_*`` wrappers
            swallow DB errors; the fake never raises, so all writes
            land deterministically).
        manager: Stub manager exposing the R2 facades.
        denied_count: Initial ``attestation_denied_count`` (the
            ``denied_count_getter`` closure captures this; the gate
            reads it via ``getter()`` on every evaluation).
        deny_bound: ``attestation_denied_count`` bound (Phase 2
            default = 3).
        window: Attestation bounded window (default = 3).
    """
    settings = GateSettings(mode="enforce", window=window, deny_bound=deny_bound)
    config = build_gate_config(
        instance_id,
        settings,
        llm_judge_enabled=False,  # this file: gate seam only, no LLM judge
    )

    def _getter() -> int:
        return denied_count

    return create_attestation_gate_node(
        config,
        settings,
        manager,
        instance_id,
        denied_count_getter=_getter,
        ledger=ledger,
    )


def _delegated_ai(target: str = "child-id") -> AIMessage:
    """AIMessage carrying ``send_message`` — anchors the conditional
    ``attestation_required=True`` arm (the delegation scan sees the
    tool call after the last real user message). Empty content +
    ``send_message`` tool call is the canonical shape."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "send_message", "args": {"target": target}, "id": "del-1"}
        ],
    )


def _clean_attest_ai(call_id: str = "att-1") -> AIMessage:
    """The CLEAN ``attest_completion`` AIMessage — EMPTY content +
    ``attest_completion`` tool call. The 2026-09-19 contract's
    required FIRST-half shape; mirrors the unit-test fixture
    ``clean_attest_ai`` in ``tests/unit/test_attestation_attest_first_
    contract.py``."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "attest_completion", "args": {}, "id": call_id}
        ],
    )


# Long standalone text report — the FINAL AIMessage that satisfies
# ``SHORT_REPORT_WORD_THRESHOLD`` (150) and clears the
# ``final_ai_is_text_report=True`` predicate. Mirrors the E2E
# fixture shape in ``test_attestation_attest_first_e2e.py``.
LONG_REPORT_TEXT: str = (
    "The four patches shipped end-to-end and the integration matrix "
    "is green on every environment we maintain. Patch 1 fixed the "
    "off-by-one in the cache TTL calculator; the unit tests now "
    "exercise both the elapsed-second and wall-clock-second "
    "boundaries at the second and minute granularity, so the edge "
    "case the cache had been silently mishandling is closed. Patch "
    "2 cleaned up the dead imports in the worker pool module after "
    "the migration, removing the legacy compatibility shim and the "
    "related test scaffolding that no longer carried its weight. "
    "Patch 3 refactored the error-reporting decorator so the "
    "stack-frame metadata is consistent across all four call sites "
    "in the graph node and the manager facade. Patch 4 added the "
    "missing operator-boot log line for the new resolver module so "
    "operators can grep the boot summary for the resolved effective "
    "values. All four patches passed their respective suites on the "
    "first run with no flake; no follow-ups outstanding; the mission "
    "is complete and ready for review by the next teammate in the "
    "chain."
)

# Defensive: confirm the long-report fixture clears the threshold.
assert len(LONG_REPORT_TEXT.split()) >= 150, (
    f"LONG_REPORT_TEXT must be >= SHORT_REPORT_WORD_THRESHOLD (150) "
    f"words; got {len(LONG_REPORT_TEXT.split())}"
)


def _make_state(
    messages: list,
    *,
    reminder_count: int | None = None,
) -> dict:
    """Build the in-node state dict the gate reads.

    The gate node reads ``state["messages"]`` (the in-node message
    list) + ``state.get(ATTESTATION_REMINDER_COUNT_KEY, 0)`` (the
    per-mission HOLD reminder counter). The thread-id is threaded
    through ``config["configurable"]["thread_id"]``.
    """
    state: dict[str, Any] = {"messages": list(messages)}
    if reminder_count is not None:
        state[ATTESTATION_REMINDER_COUNT_KEY] = reminder_count
    return state


async def _invoke(node, state: dict, *, thread_id: str) -> dict:
    """Helper — invoke the gate node with the thread-id wired through
    ``config["configurable"]["thread_id"]`` (the production seam)."""
    return await node(
        state,
        config={"configurable": {"thread_id": thread_id}},
    )


def _run(node, state: dict, *, thread_id: str) -> dict:
    """Synchronous wrapper for the async gate node (matches the
    ``tests/integration/test_attestation_user_answer_pending_lca.py``
    pattern of ``def test_*`` + ``asyncio.run``)."""
    return asyncio.run(_invoke(node, state, thread_id=thread_id))


def _canonical_log_row(caplog) -> str | None:
    """Extract the canonical ``event=leader_completion_gate ...`` row
    from caplog (the evaluate() glue emits exactly one per call)."""
    return next(
        (
            r.message
            for r in caplog.records
            if "event=leader_completion_gate " in r.message
        ),
        None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 1 — REMINDER CAP (third-HOLD behavior)
# ─────────────────────────────────────────────────────────────────────────────


class TestReminderCapThirdAttestFallsThroughToAllow:
    """Three consecutive attest-only turn-ends: each is the clean
    attest-call shape (empty content + ``attest_completion`` tool call;
    no subsequent text report). The first two HOLD with the Final
    Report Reminder; the third (cap reached) falls through to plain
    ``meta_bypass`` allow with the reminder count CLEARED to 0.
    """

    def _messages(self) -> list:
        return [
            HumanMessage(content="do the work"),
            _delegated_ai("child-1"),
        ]

    def test_three_clean_attest_turn_ends(self, caplog):
        ledger = _FakeLedger(denied_count=0)
        manager = _make_manager()
        node = _make_node(
            instance_id="test-hold-cap",
            ledger=ledger,
            manager=manager,
            denied_count=0,
        )

        # ── 1st turn-end: clean attest → HOLD + reminder (count 0→1)
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_gate"
        ):
            result_1 = _run(
                node,
                _make_state(
                    self._messages() + [_clean_attest_ai("att-1")],
                    reminder_count=0,
                ),
                thread_id="test-hold-cap",
            )

        assert result_1.get("attestation_route") == "agent", (
            f"1st HOLD must route back to agent; got {result_1.get('attestation_route')!r}"
        )
        assert "messages" in result_1 and len(result_1["messages"]) == 1
        reminder_msg_1 = result_1["messages"][0]
        assert isinstance(reminder_msg_1, HumanMessage), (
            f"HOLD injects a HumanMessage; got {type(reminder_msg_1).__name__}"
        )
        # The reminder body is the canonical CLEAN-call reminder
        # (NOT the bundled one — the attest_call had EMPTY content).
        assert reminder_msg_1.content.strip(), (
            "HOLD reminder must carry a non-empty body"
        )
        assert ATTESTATION_FINAL_REPORT_REMINDER.strip().split("\n")[0] in (
            reminder_msg_1.content
        ) or ATTESTATION_FINAL_REPORT_REMINDER[:60] in reminder_msg_1.content, (
            f"HOLD reminder must carry the canonical CLEAN-call body; "
            f"got first 60 chars: {reminder_msg_1.content[:60]!r}"
        )
        # The clean vs bundled reminder bodies share the
        # ``[SYSTEM CONTEXT: Final Report Reminder]`` title — the
        # discriminator is the BODY. The BUNDLED reminder body
        # begins with "Attestation received, but your tool-call
        # message contained text" — that substring MUST NOT appear
        # on the clean HOLD path. The CLEAN reminder body begins
        # with "Attestation received. Deliver your full detailed
        # final report now as your final message." — that substring
        # MUST appear.
        assert "Attestation received, but your tool-call message" not in (
            reminder_msg_1.content
        ), (
            "CLEAN-call HOLD must NOT carry the BUNDLED-call body "
            "(substrings 'tool-call message contained text' / "
            "'Re-issue your full detailed final report now as its own')"
        )
        assert (
            "Re-issue your full detailed final report now as its own"
            not in reminder_msg_1.content
        )
        assert "Deliver your full detailed final report now as your final message" in (
            reminder_msg_1.content
        ), (
            "CLEAN-call HOLD must carry the CLEAN body — substring "
            "'Deliver your full detailed final report now as your "
            "final message' MUST appear"
        )
        assert result_1.get(ATTESTATION_REMINDER_COUNT_KEY) == 1, (
            f"HOLD must increment the per-mission reminder counter to 1; "
            f"got {result_1.get(ATTESTATION_REMINDER_COUNT_KEY)!r}"
        )
        # The HOLD log row carries the counter-INDEPENDENT marker.
        row_1 = _canonical_log_row(caplog)
        assert row_1 is not None, "evaluate() must emit the canonical log row"
        assert "decision=hold" in row_1, (
            f"1st decision must be HOLD; got row: {row_1[:200]!r}"
        )

        # ── 2nd turn-end: same shape → HOLD + reminder (count 1→2)
        caplog.clear()
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_gate"
        ):
            result_2 = _run(
                node,
                _make_state(
                    self._messages() + [_clean_attest_ai("att-2")],
                    reminder_count=1,
                ),
                thread_id="test-hold-cap",
            )

        assert result_2.get("attestation_route") == "agent", (
            f"2nd HOLD must route back to agent; got {result_2.get('attestation_route')!r}"
        )
        assert "messages" in result_2 and len(result_2["messages"]) == 1
        assert isinstance(result_2["messages"][0], HumanMessage)
        assert result_2.get(ATTESTATION_REMINDER_COUNT_KEY) == 2, (
            f"2nd HOLD must increment to 2; got {result_2.get(ATTESTATION_REMINDER_COUNT_KEY)!r}"
        )
        row_2 = _canonical_log_row(caplog)
        assert row_2 is not None
        assert "decision=hold" in row_2

        # ── 3rd turn-end: cap reached → plain allow + counter CLEARED
        caplog.clear()
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_gate"
        ), caplog.at_level(
            logging.INFO, logger="daemon.graph"
        ):
            result_3 = _run(
                node,
                _make_state(
                    self._messages() + [_clean_attest_ai("att-3")],
                    reminder_count=2,
                ),
                thread_id="test-hold-cap",
            )

        # Plain ``meta_bypass`` allow: ZERO messages injected, route=None.
        assert "messages" not in result_3, (
            f"3rd turn-end (cap reached) must NOT inject a reminder; "
            f"got: {result_3!r}"
        )
        assert result_3.get("attestation_route") is None, (
            f"3rd turn-end (cap reached) must plain-allow (no route); "
            f"got {result_3.get('attestation_route')!r}"
        )
        # Per-mission reminder counter CLEARED to 0 on the cap fall-through.
        assert result_3.get(ATTESTATION_REMINDER_COUNT_KEY) == 0, (
            f"cap fall-through must reset the reminder counter to 0; "
            f"got {result_3.get(ATTESTATION_REMINDER_COUNT_KEY)!r}"
        )
        # The cap-fall-through structured log line — emitted at the
        # gate node's HOLD branch when prior_reminder_count >= CAP.
        cap_row = next(
            (
                r.message
                for r in caplog.records
                if "event=leader_completion_gate_hold_reminder_cap" in r.message
            ),
            None,
        )
        assert cap_row is not None, (
            "cap fall-through must emit "
            "``event=leader_completion_gate_hold_reminder_cap`` with "
            "``decision=fall_through_to_meta_bypass_allow``"
        )
        assert "decision=fall_through_to_meta_bypass_allow" in cap_row, (
            f"cap log must record the fall-through decision; got: {cap_row!r}"
        )

        # Counter-independence: ledger.attestation_denied_count never touched.
        assert ledger.denied_count == 0, (
            f"HOLD/cap-fallthrough must NOT touch attestation_denied_count; "
            f"got {ledger.denied_count}"
        )
        assert ledger.increment_calls == [], (
            f"HOLD/cap-fallthrough must NOT call ledger.increment; "
            f"got {ledger.increment_calls!r}"
        )
        assert ledger.reset_calls == [], (
            f"HOLD must NOT call ledger.reset (cap fall-through resets "
            f"only the REMINDER counter, not the deny ledger); "
            f"got {ledger.reset_calls!r}"
        )

    def test_reminder_cap_constant_is_two(self):
        """Pin the documented cap (``ATTESTATION_REMINDER_CAP == 2``)
        so the third turn-end behavior is anchored to a module-level
        constant — module-level by design (the 2026-09-19 D-entry's
        "deliberately NOT env-tunable" decision)."""
        assert ATTESTATION_REMINDER_CAP == 2, (
            f"the documented escape (ATTESTATION_REMINDER_CAP) must be 2; "
            f"got {ATTESTATION_REMINDER_CAP}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 2 — COUNTER STATIC AT BOUND
# ─────────────────────────────────────────────────────────────────────────────


class TestCounterStaticAtBoundDuringHoldCycles:
    """``attestation_denied_count`` is COUNTER-INDEPENDENT on HOLD:
    the bound / escalation machinery is NEVER consulted and the
    counter NEVER increments, even at deny_bound-1. A subsequent
    proper attested allow (clean attest + standalone long report)
    triggers the attested-allow reset (trigger 1).
    """

    def test_two_hold_cycles_keep_counter_static_then_attested_allow_resets(
        self, caplog
    ):
        instance_id = "test-counter-bound"
        # Prime the ledger by simulating 2 prior DENIED turns (the
        # "via prior DENIED turns" precondition from the contract).
        # This raises the counter to deny_bound-1 (= 2 when
        # deny_bound = 3), which would be the trigger zone for
        # ``terminal_after_bound`` on a DENIED path.
        ledger = _FakeLedger(denied_count=0)
        ledger.increment(instance_id, "epoch-pre-deny-1")
        ledger.increment(instance_id, "epoch-pre-deny-2")
        assert ledger.denied_count == 2, (
            f"prime step must raise the ledger counter to 2; "
            f"got {ledger.denied_count}"
        )

        manager = _make_manager()
        node = _make_node(
            instance_id=instance_id,
            ledger=ledger,
            manager=manager,
            denied_count=ledger.denied_count,
        )

        base_messages = [
            HumanMessage(content="do the work"),
            _delegated_ai("child-1"),
        ]

        # ── HOLD cycle 1 — counter MUST stay at 2
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_gate"
        ):
            result_1 = _run(
                node,
                _make_state(
                    base_messages + [_clean_attest_ai("att-1")],
                    reminder_count=0,
                ),
                thread_id=instance_id,
            )

        assert result_1.get("attestation_route") == "agent"
        assert "messages" in result_1
        assert result_1.get(ATTESTATION_REMINDER_COUNT_KEY) == 1
        assert ledger.denied_count == 2, (
            f"counter MUST stay at 2 across the 1st HOLD (counter-independence); "
            f"got {ledger.denied_count}"
        )
        # No bound/escalation interaction: the ``terminal_after_bound``
        # log row is the documented operator signal that the bound
        # machinery fired. Its absence on HOLD is the proof.
        terminal_rows = [
            r.message
            for r in caplog.records
            if "event=leader_completion_gate_terminal_after_bound" in r.message
        ]
        assert terminal_rows == [], (
            f"HOLD must NOT trigger the bound/escalation machinery; "
            f"got terminal rows: {terminal_rows!r}"
        )

        # ── HOLD cycle 2 — counter STILL at 2
        caplog.clear()
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_gate"
        ):
            result_2 = _run(
                node,
                _make_state(
                    base_messages + [_clean_attest_ai("att-2")],
                    reminder_count=1,
                ),
                thread_id=instance_id,
            )

        assert result_2.get("attestation_route") == "agent"
        assert result_2.get(ATTESTATION_REMINDER_COUNT_KEY) == 2
        assert ledger.denied_count == 2, (
            f"counter MUST still stay at 2 across the 2nd HOLD; "
            f"got {ledger.denied_count}"
        )

        # ── Proper attested allow — clean attest + standalone long report.
        # Final AI = the long text report (no tool calls,
        # >= SHORT_REPORT_WORD_THRESHOLD words). The gate's
        # ``decide()`` step (2) is the attested-allow branch with
        # counter RESET (trigger 1).
        caplog.clear()
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_gate"
        ):
            result_3 = _run(
                node,
                _make_state(
                    # Same attest + new standalone report → the
                    # attestation is STILL in-window (window=3) and
                    # the FINAL AI is a standalone text report.
                    base_messages
                    + [
                        _clean_attest_ai("att-3"),
                        AIMessage(content=LONG_REPORT_TEXT),
                    ],
                    reminder_count=2,
                ),
                thread_id=instance_id,
            )

        assert "messages" not in result_3, (
            f"attested allow (clean attest + long report) must plain-allow; "
            f"got: {result_3!r}"
        )
        assert result_3.get("attestation_route") is None
        # Counter RESET to 0 on the attested-allow branch.
        assert result_3.get(ATTESTATION_REMINDER_COUNT_KEY) == 0
        # Ledger counter RESET to 0 via ``safe_reset(ledger, instance_id)``.
        assert ledger.denied_count == 0, (
            f"attested-allow must RESET ledger.denied_count to 0; "
            f"got {ledger.denied_count}"
        )
        assert instance_id in ledger.reset_calls, (
            f"attested-allow must call ledger.reset({instance_id!r}); "
            f"got calls: {ledger.reset_calls!r}"
        )
        # The canonical log row records ALLOWED + attestation_present=True.
        row_3 = _canonical_log_row(caplog)
        assert row_3 is not None
        assert "decision=allowed" in row_3, (
            f"proper attested allow must log decision=allowed; got row: {row_3[:200]!r}"
        )
        assert "attestation_present=True" in row_3, (
            f"proper attested allow must record attestation_present=True; "
            f"got row: {row_3[:200]!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 3 — NO-ATTESTATION PRECEDENCE (both constructed)
# ─────────────────────────────────────────────────────────────────────────────


class TestNoAttestationPrecedence:
    """The legitimate-pending inputs PRECEDE the attested/HOLD step:
    plain allow, ZERO reminder/nudge/hint, ZERO counter movement.

    (i) ``user_answer_pending=True`` — the FIFTH legitimate-pending
    input (FIX-2, incident 6a0d60c9). Fires BEFORE the attested check
    in ``decide()`` step (1); the awaiting USER answer is the whole
    turn's purpose.

    (ii) branch-5/R2 legitimate-pending — ``pending_children>0`` (or
    ``queued_or_expected_wakeups>0``) is one of the THREE R2 inputs
    (``pending_children``, ``queued_or_expected_wakeups``,
    ``live_descendants``) the gate reads via the manager facades. The
    gate's ``decide()`` step (3) returns
    ``ALLOWED_LEGITIMATE_PENDING_WAKEUP`` BEFORE the un-attested deny
    path. This case is the delegation turn-end class (mid-mission
    with delegation; the leader has not yet issued ``attest_completion``
    — the canonical R2 precedence over the un-attested deny path).
    """

    def test_user_answer_pending_precedes_hold_branch(self, caplog):
        """Constructed would-HOLD shape (clean attest with no
        subsequent text) PLUS ``user_answer_pending=True`` → STILL
        plain ALLOW with NO reminder, NO nudge, ZERO counter movement.

        The HOLD branch must NEVER fire when the FIFTH
        legitimate-pending input is open.
        """
        instance_id = "test-answer-precedence"
        ledger = _FakeLedger(denied_count=0)
        manager = _make_manager(answer_pending=True)
        node = _make_node(
            instance_id=instance_id,
            ledger=ledger,
            manager=manager,
            denied_count=0,
        )

        # Would-HOLD shape (without answer-pending): delegation +
        # clean attest_call, no subsequent text report.
        messages = [
            HumanMessage(content="please review and give me a go/no-go"),
            _delegated_ai("child-1"),
            _clean_attest_ai("att-1"),
        ]

        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_gate"
        ):
            result = _run(
                node,
                _make_state(messages, reminder_count=0),
                thread_id=instance_id,
            )

        # Plain allow — NO messages, NO route.
        assert "messages" not in result, (
            f"user_answer_pending=True must plain-allow (no nudge, no reminder); "
            f"got: {result!r}"
        )
        assert result.get("attestation_route") is None
        # Ledger untouched.
        assert ledger.denied_count == 0
        assert ledger.increment_calls == [], (
            f"answer-pending path must NOT call ledger.increment; "
            f"got: {ledger.increment_calls!r}"
        )
        assert ledger.reset_calls == []
        # Canonical row records the precedence: decision =
        # allowed_legitimate_pending_wakeup, user_answer_pending=True.
        row = _canonical_log_row(caplog)
        assert row is not None
        assert "decision=allowed_legitimate_pending_wakeup" in row, (
            f"FIX-2 precedence: the answer-pending arm resolves to "
            f"allowed_legitimate_pending_wakeup; got row: {row[:200]!r}"
        )
        assert "user_answer_pending=True" in row
        assert "attestation_present=True" in row, (
            f"the attest call IS still in-window (scanner finds it "
            f"before decide() step (1) fires); row: {row[:200]!r}"
        )

    def test_pending_children_precedes_unattested_deny(self, caplog):
        """R2 legitimate-pending (branch 5) — ``pending_children>0``
        BEFORE the un-attested deny path (the delegation turn-end
        class). Constructed: leader dispatched a child, no
        ``attest_completion`` call yet, pending_children=1 →
        ``ALLOWED_LEGITIMATE_PENDING_WAKEUP``.

        This is the CANONICAL R2 precedence over deny — a leader
        mid-mission with running children MUST NOT be denied for
        missing attestation while legitimate pending work is open.
        """
        instance_id = "test-pending-precedence"
        ledger = _FakeLedger(denied_count=0)
        manager = _make_manager(pending_children=1)
        node = _make_node(
            instance_id=instance_id,
            ledger=ledger,
            manager=manager,
            denied_count=0,
        )

        # Mid-mission turn-end: delegation, no attest call yet
        # (the leader is still running its first tool turn; not yet
        # at a completion-check candidate).
        messages = [
            HumanMessage(content="do the work"),
            _delegated_ai("child-1"),
        ]

        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_gate"
        ):
            result = _run(
                node,
                _make_state(messages, reminder_count=0),
                thread_id=instance_id,
            )

        # Plain allow — NO nudge, NO route (R2 non-reset semantics).
        assert "messages" not in result, (
            f"R2 legitimate-pending must plain-allow; got: {result!r}"
        )
        assert result.get("attestation_route") is None
        # Ledger untouched (R2 non-reset IS the loop protection —
        # counter must NOT increment).
        assert ledger.denied_count == 0
        assert ledger.increment_calls == [], (
            f"R2 legitimate-pending must NOT increment the deny counter "
            f"(the R2 non-reset IS the loop protection); "
            f"got: {ledger.increment_calls!r}"
        )
        assert ledger.reset_calls == []
        # Canonical row records the precedence.
        row = _canonical_log_row(caplog)
        assert row is not None
        assert "decision=allowed_legitimate_pending_wakeup" in row, (
            f"R2 legitimate-pending must resolve to "
            f"allowed_legitimate_pending_wakeup; got row: {row[:200]!r}"
        )
        assert "pending_children=1" in row, (
            f"the row must surface the R2 input value for forensics; "
            f"row: {row[:200]!r}"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "R2 pending_children does not precede the attested/HOLD step; "
            "only user_answer_pending does; surfaced 2026-09-20 merge gate; "
            "pending leader ratification (decide() step order 1→2→3)"
        ),
    )
    def test_user_described_attest_plus_pending_precedes_hold(
        self, caplog
    ):
        """CONTRACT ASSERTION — the user-described scenario (ii):
        ``pending_children>0`` AND ``attest_completion`` IN-window AND
        no subsequent standalone text report → MUST ALLOW without HOLD
        (the R2 legitimate-pending input is supposed to precede the
        attested/HOLD step).

        Constructed EXACTLY per the user prompt:
            * ``attest_completion`` is present in the bounded window
              (the clean attest_call AIMessage is the LAST AI — the
              same shape that triggers HOLD when pending_children=0).
            * ``pending_children>0`` (children still running; the
              leader's report is not yet due).
            * NO subsequent standalone text report.

        Expected per the contract text: ALLOW (no message injected,
        no route). The current code's ``decide()`` step (2) — the
        attested check — fires BEFORE the R2 legitimate-pending step
        (3), so the gate returns ``Decision.HOLD`` whenever
        ``attested=True`` and ``final_ai_is_text_report=False``,
        regardless of pending_children. THIS IS A CONTRACT DEVIATION
        if the assertion fails.
        """
        instance_id = "test-attest-plus-pending-contract"
        ledger = _FakeLedger(denied_count=0)
        manager = _make_manager(pending_children=1)
        node = _make_node(
            instance_id=instance_id,
            ledger=ledger,
            manager=manager,
            denied_count=0,
        )

        # Exactly the user-described state:
        #   * delegation message (gate ON, attestation_required=True)
        #   * clean ``attest_completion`` AIMessage (attested=True)
        #   * NO subsequent standalone text report
        #   * pending_children=1 (the leader has running children)
        messages = [
            HumanMessage(content="do the work"),
            _delegated_ai("child-1"),
            _clean_attest_ai("att-1"),
        ]

        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_gate"
        ), caplog.at_level(
            logging.INFO, logger="daemon.graph"
        ):
            result = _run(
                node,
                _make_state(messages, reminder_count=0),
                thread_id=instance_id,
            )

        # Contract expectation: ALLOW (no message, no route).
        # If the gate injects a HumanMessage or routes back to agent,
        # the precedence HAS NOT been wired and this is a contract
        # deviation to report (the user said: "if semantics look
        # WRONG, report with evidence, do not fix").
        if "messages" in result:
            pytest.fail(
                "CONTRACT DEVIATION (scenario 3(ii) exact shape): "
                "pending_children>0 + attested + no report was "
                "expected to ALLOW without HOLD per the user prompt; "
                "the gate injected a message instead "
                f"(decision-driven; see caplog). result={result!r}"
            )
        assert result.get("attestation_route") is None, (
            f"contract expectation: NO route on ALLOW; got "
            f"{result.get('attestation_route')!r}"
        )
        # Ledger untouched on the legitimate-pending path.
        assert ledger.denied_count == 0
        assert ledger.increment_calls == []
        assert ledger.reset_calls == []
        # Canonical row records the precedence decision.
        row = _canonical_log_row(caplog)
        assert row is not None
        assert "pending_children=1" in row, (
            f"the row must surface the R2 input value; row: {row[:200]!r}"
        )
        assert "attestation_present=True" in row, (
            f"the attest call IS in-window; row: {row[:200]!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 4 — WINDOW INTEGRITY
# ─────────────────────────────────────────────────────────────────────────────


class TestWindowIntegrityReportAfterAttestReminder:
    """Attest turn (HOLD+reminder) → report turn (NO new attest call)
    → attestation STILL in-window (window=3) → ALLOWED at the report
    turn-end (NOT a fresh deny).

    Constructed message list at the report turn-end:
        [0] HumanMessage(task)
        [1] AIMessage(delegation)
        [2] AIMessage(clean attest_completion)        ← the attest call
        [3] HumanMessage(reminder, server-injected)   ← the HOLD reminder
        [4] AIMessage(LONG_REPORT_TEXT)               ← final AI

    The scanner walks the last 3 AIMessages (window=3): the
    delegation_ai + the attest_ai + the report_ai. The attest_ai is
    still in-window; the final_ai is the report (NO tool calls, >=
    150 words). ``decide()`` step (2) attested + text_report →
    ``Decision.ALLOWED`` with counter RESET (trigger 1).
    """

    def test_attestation_in_window_at_report_turn_end_allows(self, caplog):
        instance_id = "test-window-integrity"
        ledger = _FakeLedger(denied_count=0)
        manager = _make_manager()
        node = _make_node(
            instance_id=instance_id,
            ledger=ledger,
            manager=manager,
            denied_count=0,
        )

        # The report turn-end message list. The reminder HumanMessage
        # is the canonical server-injected HOLD reminder from the
        # PRIOR turn (the field set mirrors the factory in
        # ``graph.py:_make_attestation_final_report_reminder_message``).
        report_turn_messages = [
            HumanMessage(content="do the work"),
            _delegated_ai("child-1"),
            _clean_attest_ai("att-1"),
            HumanMessage(
                content="Final Report Reminder\n... deliver your report",
                additional_kwargs={
                    "injected_message": True,
                    "attestation_final_report_reminder": True,
                },
            ),
            AIMessage(content=LONG_REPORT_TEXT),  # final AI = long report
        ]

        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_gate"
        ):
            result = _run(
                node,
                _make_state(report_turn_messages, reminder_count=1),
                thread_id=instance_id,
            )

        # Decision: ALLOWED — attestation_present=True (attest_ai in
        # window=3) AND final_ai_is_text_report=True (the long
        # standalone report has NO tool calls and >= 150 words).
        assert "messages" not in result, (
            f"attested allow (clean attest + long report) must plain-allow; "
            f"got: {result!r}"
        )
        assert result.get("attestation_route") is None
        # Per-mission reminder counter RESET to 0 on attested allow.
        assert result.get(ATTESTATION_REMINDER_COUNT_KEY) == 0, (
            f"attested allow must reset the reminder counter to 0; "
            f"got {result.get(ATTESTATION_REMINDER_COUNT_KEY)!r}"
        )
        # NOT a fresh deny — counter did NOT increment.
        assert ledger.denied_count == 0, (
            f"attested allow must NOT increment the deny counter; "
            f"got {ledger.denied_count}"
        )
        assert ledger.increment_calls == [], (
            f"attested allow must NOT call ledger.increment; "
            f"got: {ledger.increment_calls!r}"
        )
        # The attested-allow reset DOES call ledger.reset (the
        # attested-allow reset trigger 1 is the SAME reset on the
        # ledger the prior-contract implementations used).
        assert instance_id in ledger.reset_calls, (
            f"attested allow must call ledger.reset({instance_id!r}); "
            f"got: {ledger.reset_calls!r}"
        )

        # Canonical row records the window-integrity outcome.
        row = _canonical_log_row(caplog)
        assert row is not None
        assert "decision=allowed" in row, (
            f"window-integrity outcome must log decision=allowed; "
            f"got row: {row[:200]!r}"
        )
        assert "attestation_present=True" in row, (
            f"window=3 must surface attestation_present=True at the "
            f"report turn-end (attest call sits 2-3 AIMessages back "
            f"including the reminder HumanMessage); got row: {row[:200]!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Module-level smoke — make the harness importable in isolation.
# ─────────────────────────────────────────────────────────────────────────────


def test_module_imports():
    """Sanity — the module imports cleanly under the production
    resolvers (no env mutation)."""
    from daemon.graph import (  # noqa: F401
        ATTESTATION_REMINDER_CAP,
        ATTESTATION_REMINDER_COUNT_KEY,
        create_attestation_gate_node,
    )
    from daemon.services.attestation_gate import (  # noqa: F401
        GateSettings,
        build_gate_config,
        decide,
        evaluate,
    )
    assert ATTESTATION_REMINDER_CAP == 2
    assert ATTESTATION_REMINDER_COUNT_KEY == "attestation_reminder_count"
