"""Unit tests for the completion-gate bounds fix (P1-P4).

Closing DEFECT critical-note ``f018da70`` — the attestation gate's
'teminal-withheld' epoch (judge_invoked=False) is an UNBOUNDED deny
loop. P1-P4 add per-dispatch bounds + content-shape guards + nudge
persistence so a leader mission cannot run-away mute.

Each guard lives in production code with a single canonical
counterpart (no double bookkeeping, no per-test stubbing):

* P1 — ``daemon.services.attestation_gate.withhold_budget_exhausted``
  + ``daemon.graph.WITHHOLD_DENY_BUDGET`` (constant). Composition
  gate in ``daemon.graph.create_attestation_gate_node`` consults
  the predicate; ``ATTESTATION_WITHHOLD_DENY_COUNT_KEY`` carries the
  per-mission counter.
* P2 — ``daemon.graph._normalize_bare_content`` + the
  consecutive-identical channel pair. DENIED branch picks the repair
  body via ``ATTESTATION_IDENTICAL_REPAIR_NUDGE_TEXT`` on
  first-strike and force-terminalizes on second-strike.
* P3 — ``daemon.graph._is_degenerate_shape`` + the degenerate-streak
  channel. Active in deny-loop only; loud terminal on second-streak
  crossing.
* P4 — the gate's log rows are the events; the new channels carry
  the persisted state for cross-turn visibility.

The tests exercise the real gate node closure shape
(``create_attestation_gate_node``) with a MagicMock manager and
patched judge — the same pattern as
``tests/unit/test_attestation_judge_wiring.py``. A few tests use
the lower-level helpers directly (whitespace normalization +
degenerate shape) for the cheap pure-function cases.

Run the FULL attestation test dirs to avoid the merge-window
scoped-pack gate hazard noted in the task: ``tests/unit`` and
``tests/integration`` attestation files plus the gate-related tests
in ``tests/job_queue``. NOT the whole repo suite.
"""
from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.graph import (
    ATTESTATION_ANY_SUBSTANTIVE_KEY,
    ATTESTATION_CONSECUTIVE_IDENTICAL_KEY,
    ATTESTATION_DEGENERATE_STREAK_KEY,
    ATTESTATION_DENY_PROGRESS_KEY,
    ATTESTATION_IDENTICAL_REPAIR_NUDGE_TEXT,
    ATTESTATION_LAST_BARE_AI_CONTENT_KEY,
    ATTESTATION_NUDGE_TEXT,
    ATTESTATION_REPAIR_NUDGE_SENT_KEY,
    ATTESTATION_WITHHOLD_DENY_COUNT_KEY,
    CONSECUTIVE_IDENTICAL_THRESHOLD,
    DEGENERATE_STREAK_THRESHOLD,
    DEGENERATE_WORD_THRESHOLD,
    WITHHOLD_DENY_BUDGET,
    _is_degenerate_shape,
    _normalize_bare_content,
    create_attestation_gate_node,
)
from daemon.services import (
    attestation_report_judge as judge_mod,
)
from daemon.services.attestation_gate import (
    Decision,
    GateSettings,
    WITHHOLD_DENY_BUDGET_DEFAULT,
    build_gate_config,
    withhold_budget_exhausted,
)
from daemon.services.attestation_judge_resolver import (
    reset_llm_judge_resolver_for_tests,
)
from daemon.services.attestation_resolver import (
    reset_attestation_resolver_for_tests,
)


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures — hermetic kill-switch isolation (NIT-7 mirror)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_resolvers(monkeypatch):
    """Hermetic isolation per-test.

    Clear the LLM-judge kill-switch env var + both cached
    resolvers so an outer ``.env`` / CI runner that flips the
    kill-switch OFF cannot leak in and silently disable the
    judge for the deny-path assertions.
    """
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
    )
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()


def _make_gate_node(
    *,
    instance_id: str,
    denied_count_getter=None,
    ledger=None,
    gate_settings=None,
) -> tuple:
    """Build the gate node with manager + ledger stubs (no real DB)."""
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

    settings = gate_settings or GateSettings("enforce", 3, 3)
    config = build_gate_config(instance_id, settings)
    node = create_attestation_gate_node(
        config,
        settings,
        manager,
        instance_id,
        denied_count_getter=denied_count_getter or (lambda: 0),
        ledger=ledger,
    )
    return node, manager, ledger


def _delegated_without_attest_messages(final_text: str) -> dict:
    """Standard delegated mission shape: real user → AI send_message → AI done.

    The FINAL AIMessage is bare (no tool calls) — the precondition
    for the P2 consecutive-identical guard (the bare-no-tool-call
    restriction scopes the guard strictly).
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


# Stub LLM judge invoker that simulates the kill-switch-off path
# (judge never invoked). The composition gate then converts each
# bound exhaustion to DENIED — the never-spoke arc the withhold
# budget is designed to bound.
async def _judge_never_called(
    config, user_payload, *, timeout_s, system_prompt=None
):
    raise AssertionError(
        "judge must not be invoked on the budget-exhaustion path"
    )


# ─────────────────────────────────────────────────────────────────────────────
# (A) PURE-FUNCTION HELPERS — normalization + degenerate shape
# ─────────────────────────────────────────────────────────────────────────────


class TestNormalizeBareContent:
    """The P2 normalization helper.

    Pure function over the FINAL AIMessage ``content`` field.
    Whitespace-insensitive (leading / trailing / internal
    collapsing). Empty / None inputs read as the empty string.
    """

    def test_normalizes_whitespace_runs(self):
        # Three literal-newline characters ('\\n' is the two-
        # character sequence, '\n' is a single newline char).
        assert (
            _normalize_bare_content("  hello\n\n  world  ")
            == "hello world"
        )

    def test_preserves_emoji_only(self):
        # The 34978dfc mute wall was single-emoji; the normalization
        # preserves it so the consecutive-identical comparison
        # catches the repeat.
        assert _normalize_bare_content("🔇") == "🔇"
        assert _normalize_bare_content("  🔇  ") == "🔇"

    def test_none_reads_as_empty(self):
        assert _normalize_bare_content(None) == ""

    def test_empty_string_reads_as_empty(self):
        assert _normalize_bare_content("") == ""

    def test_idempotent(self):
        # Running normalize twice is the identity. Real tab
        # characters ('\t') collapse to single spaces just like
        # runs of spaces.
        once = _normalize_bare_content("foo  bar\tbaz")
        twice = _normalize_bare_content(once)
        assert once == twice == "foo bar baz"

    def test_preserves_literal_backslash_n(self):
        # Documented behavior: the normalization does NOT decode
        # the two-character escape "\\n" — only REAL whitespace
        # characters collapse. ``str.split()`` splits on
        # whitespace; the literal backslash + n is not
        # whitespace so it stays intact.
        assert _normalize_bare_content("foo\\nbar") == "foo\\nbar"


class TestIsDegenerateShape:
    """The P3 degenerate-shape classifier (P3 gradient family).

    Bare emoji-only OR non-empty with <= DEGENERATE_WORD_THRESHOLD
    whitespace-separated tokens AND no tool calls. Tool-call
    messages always read as not-degenerate.
    """

    def test_emoji_only_is_degenerate(self):
        assert _is_degenerate_shape("🔇", None) is True

    def test_mixed_emoji_is_degenerate(self):
        assert _is_degenerate_shape("🔇🔇🔇", None) is True

    def test_single_word_is_degenerate(self):
        # "Done." has 1 word ≤ DEGENERATE_WORD_THRESHOLD (3).
        assert _is_degenerate_shape("Done.", None) is True

    def test_two_words_is_degenerate(self):
        assert _is_degenerate_shape("All done.", None) is True

    def test_three_words_is_degenerate(self):
        assert (
            _is_degenerate_shape("All work finished.", None) is True
        )

    def test_four_words_is_not_degenerate(self):
        assert (
            _is_degenerate_shape(
                "All four patches shipped.", None
            )
            is False
        )

    def test_long_prose_is_not_degenerate(self):
        assert (
            _is_degenerate_shape(
                "The implementation is complete and verified across "
                "the integration matrix.",
                None,
            )
            is False
        )

    def test_tool_calls_break_degenerate(self):
        # A leader that interleaves tool calls is doing real
        # work — even if its prose is brief, the classifier
        # returns False so the P3 streak resets.
        assert (
            _is_degenerate_shape(
                "Done.", [{"name": "send_message", "args": {}, "id": "c1"}]
            )
            is False
        )

    def test_empty_content_not_degenerate(self):
        # Empty bare reply is NOT degenerate per the spec — a
        # bare empty reply is structurally different from a
        # degenerate-shape reply. (The P2 guard catches bare
        # repeats; P3 only catches the gradient family.)
        assert _is_degenerate_shape("", None) is False

    def test_none_content_not_degenerate(self):
        assert _is_degenerate_shape(None, None) is False


# ─────────────────────────────────────────────────────────────────────────────
# (B) WITHHOLD BUDGET PREDICATE — single source of truth
# ─────────────────────────────────────────────────────────────────────────────


class TestWithholdBudgetExhausted:
    """P1 predicate — single source of truth for the per-dispatch
    withhold budget. Mirrors ``deny_bound_exceeded`` discipline
    verbatim.

    The default budget is :data:`WITHHOLD_DENY_BUDGET_DEFAULT`
    (= 8). Extending the budget is an explicit constant edit +
    deliberate test review (the constant's docstring pins the
    justification of the value).
    """

    def test_default_matches_constant(self):
        assert WITHHOLD_DENY_BUDGET_DEFAULT == WITHHOLD_DENY_BUDGET

    def test_default_value_is_eight(self):
        # Single-digit per task constraint; justified in the
        # constant's docstring. Pin the value here so any future
        # change forces a deliberate update.
        assert WITHHOLD_DENY_BUDGET_DEFAULT == 8

    def test_zero_does_not_exhaust(self):
        assert withhold_budget_exhausted(0) is False

    def test_seven_does_not_exhaust(self):
        assert withhold_budget_exhausted(7) is False

    def test_eight_exhausts(self):
        # 8 + 1 > 8 ⇒ True.
        assert withhold_budget_exhausted(8) is True

    def test_custom_budget(self):
        assert withhold_budget_exhausted(2, budget=3) is False
        assert withhold_budget_exhausted(3, budget=3) is True


# ─────────────────────────────────────────────────────────────────────────────
# (C) P1 — withhold budget — gate node integration
# ─────────────────────────────────────────────────────────────────────────────


class TestWithholdBudgetGateNode:
    """P1 end-to-end via the gate node (real closure shape).

    Scenarios:
    * never-spoke arc + budget not exhausted ⇒ composition gate
      converts to DENIED (the v3 ruling holds), the withhold
      counter increments, the gate routes back to ``agent``
    * never-spoke arc + budget exhausted ⇒ composition gate KEEPS
      the terminal_after_bound decision, the standard loud-exit
      machinery fires (escalation write + counter reset), the
      ``leader_completion_gate_withholding_exit`` row is emitted
    """

    def test_first_never_spoke_denies_and_increments_withhold_counter(
        self, monkeypatch, caplog
    ):
        """First never-spoke arc composition-gate conversion:
        deny+nudge, withhold counter 0 → 1, no loud exit yet."""
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, ledger = _make_gate_node(
            instance_id="p1-first-it",
            denied_count_getter=lambda: 3,  # bound exhausted at 3
        )

        with caplog.at_level("INFO", logger="daemon.graph"):
            result = asyncio.run(
                node(
                    _delegated_without_attest_messages(
                        "I am done without attesting"
                    ),
                    config={
                        "configurable": {"thread_id": "p1-first-it"}
                    },
                )
            )

        # Composition gate converted TERMINAL_AFTER_BOUND → DENIED
        # (v3 ruling preserved; the v3 ruling is the never-spoke
        # continuation that the task says we keep).
        assert result["attestation_route"] == "agent"
        # Withhold counter increments (the per-dispatch budget state).
        assert (
            result[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] == 1
        )
        # Nudge still injects (v3 ruling's never-spoke continuation
        # rides the same DENIED branch).
        assert (
            result["messages"][0].additional_kwargs[
                "attestation_nudge"
            ]
            is True
        )
        # No escalation write — the loud-exit machinery didn't
        # fire yet.
        ledger.set_escalated_and_reset.assert_not_called()
        ledger.increment.assert_called_once()
        # Greppable audit row.
        log_text = "\n".join(rec.getMessage() for rec in caplog.records)
        assert (
            "event=leader_completion_gate_bound_exhausted_never_spoke"
            in log_text
        )
        # Withhold counter surfaces in the audit row.
        assert (
            "withhold_deny_count=0" in log_text
            and "next_withhold_deny_count=1" in log_text
        )

    def test_budget_exhausted_loud_terminal_with_diagnosis(
        self, monkeypatch, caplog
    ):
        """At the 8th composition-gate conversion the budget
        exhausts: decision reverts to TERMINAL_AFTER_BOUND (the
        loud exit fires), the standard escalation write lands,
        the ``leader_completion_gate_withholding_exit`` audit row
        surfaces, and the deny+nudge loop is finally closed."""
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, ledger = _make_gate_node(
            instance_id="p1-exhaust-it",
            # bound exhausted (3) AND per-dispatch budget exhausted (8)
            # ⇒ the loud-exit branch fires.
            denied_count_getter=lambda: 3,
        )
        # Pre-stage the withhold counter at the budget cap so the
        # FIRST evaluation exhausts it (8 + 1 > 8).
        initial_state = _delegated_without_attest_messages(
            "I am done without attesting"
        )
        initial_state[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] = 8

        with caplog.at_level("INFO", logger="daemon.graph"):
            result = asyncio.run(
                node(
                    initial_state,
                    config={
                        "configurable": {"thread_id": "p1-exhaust-it"}
                    },
                )
            )

        # Loud exit — decision is TERMINAL_AFTER_BOUND, route is None.
        assert result["attestation_route"] is None
        # Standard escalation machinery wrote the flag.
        ledger.set_escalated_and_reset.assert_called_once()
        # The withhold counter resets on the loud terminal.
        assert (
            result[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] == 0
        )
        # Greppable audit rows — the P1-specific row AND the
        # canonical terminal row both fire.
        log_text = "\n".join(rec.getMessage() for rec in caplog.records)
        assert (
            "event=leader_completion_gate_withholding_exit"
            in log_text
        )
        assert (
            "event=leader_completion_gate_terminal_after_bound"
            in log_text
        )
        # The withheld-terminal row does NOT fire — the loud exit
        # replaced it.
        assert (
            "event=leader_completion_gate_bound_exhausted_never_spoke"
            not in log_text
        )

    def test_budget_exhaustion_does_not_block_recursion_limit(
        self, monkeypatch, caplog
    ):
        """The P1 budget MUST be the terminator when the
        never-spoke arc runs away — recursion_limit must never
        be the only terminator (the task's first hard
        constraint).

        This test drives the gate at a hypothetical cap-1
        configuration to make the budget fire FIRST and asserts
        the gate returns END with the loud-exit machinery
        rather than raising / looping.
        """
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, ledger = _make_gate_node(
            instance_id="p1-no-loop-it",
            denied_count_getter=lambda: 3,
        )
        # Pre-stage at the budget cap so the first evaluation
        # exhausts it.
        state = _delegated_without_attest_messages(
            "I am done without attesting"
        )
        state[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] = (
            WITHHOLD_DENY_BUDGET_DEFAULT
        )

        with caplog.at_level("INFO", logger="daemon.graph"):
            # Single evaluation — must NOT raise.
            result = asyncio.run(
                node(
                    state,
                    config={
                        "configurable": {"thread_id": "p1-no-loop-it"}
                    },
                )
            )

        assert result["attestation_route"] is None
        ledger.set_escalated_and_reset.assert_called_once()
        # Channel reset on the loud terminal.
        assert (
            result[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] == 0
        )


# ─────────────────────────────────────────────────────────────────────────────
# (D) P2 — consecutive-identical guard
# ─────────────────────────────────────────────────────────────────────────────


class TestConsecutiveIdenticalGuard:
    """P2 consecutive-identical-content guard (mute-wall catcher).

    Scenarios:
    * 1st / 2nd identical bare reply ⇒ counter increments, no
      guard action (normal deny+nudge)
    * 3rd identical bare reply + repair_nudge_sent=False ⇒
      emit the repair nudge body (the standard / directive
      selection is overridden)
    * next identical bare reply + repair_nudge_sent=True ⇒
      loud terminal (TERMINAL_AFTER_BOUND + escalation)
    * identical replies with tool calls interleaved ⇒ counter
      resets (real work breaks the streak)
    """

    def test_first_deny_normal_no_guard_fires(
        self, monkeypatch, caplog
    ):
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, _ledger = _make_gate_node(
            instance_id="p2-first-it",
            denied_count_getter=lambda: 0,
        )
        with caplog.at_level("INFO", logger="daemon.graph"):
            result = asyncio.run(
                node(
                    _delegated_without_attest_messages("Done."),
                    config={
                        "configurable": {"thread_id": "p2-first-it"}
                    },
                )
            )
        # First bare reply → counter starts at 1, no guard action.
        assert (
            result[ATTESTATION_CONSECUTIVE_IDENTICAL_KEY] == 1
        )
        assert (
            result[ATTESTATION_LAST_BARE_AI_CONTENT_KEY] == "Done."
        )
        # Repair nudge not yet sent.
        assert result[ATTESTATION_REPAIR_NUDGE_SENT_KEY] is False
        # Standard / directive nudge (NOT the repair body — the
        # threshold is not crossed yet).
        nudge = result["messages"][0]
        assert nudge.content == ATTESTATION_NUDGE_TEXT

    def test_third_identical_emits_repair_nudge(
        self, monkeypatch, caplog
    ):
        """3rd identical bare reply ⇒ repair-nudge body (overrides
        standard / directive selection). The repair-nudge-sent
        flag flips True so the next identical reply loud-terminalizes.
        """
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, _ledger = _make_gate_node(
            instance_id="p2-third-it",
            denied_count_getter=lambda: 0,
        )
        # The 34978dfc mute-wall shape (after the initial
        # delegation): 3 consecutive bare AIMessages with
        # identical content — no tool calls between them
        # (the leader has gone silent). The state carries the
        # prior turn's fingerprint so the current evaluation
        # increments to 3 and crosses the threshold. The
        # delegation AI earlier in the messages list arms the
        # conditional gate (attestation_required=True) — without
        # a delegation the gate would allow and the P2 guard
        # would not fire.
        state = {
            "messages": [
                HumanMessage(content="please do it"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "send_message",
                            "args": {"target": "child-id"},
                            "id": "c1",
                        }
                    ],
                ),
                AIMessage(content="🔇", id="m-1"),
                AIMessage(content="🔇", id="m-2"),
                AIMessage(content="🔇", id="m-3"),
            ],
            ATTESTATION_CONSECUTIVE_IDENTICAL_KEY: 2,
            ATTESTATION_LAST_BARE_AI_CONTENT_KEY: "🔇",
            ATTESTATION_REPAIR_NUDGE_SENT_KEY: False,
        }

        with caplog.at_level("INFO", logger="daemon.graph"):
            result = asyncio.run(
                node(
                    state,
                    config={
                        "configurable": {"thread_id": "p2-third-it"}
                    },
                )
            )

        # Counter incremented to 3 — crosses threshold (the
        # decision-time value before the channel write resets it).
        # The channel write RESETS to 0 on first-strike (the
        # repair-nudge is a fresh-episode break — the leader's
        # next reply is compared against NOTHING, not the prior
        # identical chain). The pre-reset value of 3 is captured
        # in the audit log row.
        assert (
            result[ATTESTATION_CONSECUTIVE_IDENTICAL_KEY] == 0
        )
        # The pre-reset value surfaces in the audit log.
        log_text = "\n".join(rec.getMessage() for rec in caplog.records)
        assert (
            "event=leader_completion_gate_consecutive_identical_nudge"
            in log_text
        )
        assert "consecutive_identical=3" in log_text
        # Repair body rides the nudge (NOT the standard / directive).
        nudge = result["messages"][0]
        assert nudge.content == ATTESTATION_IDENTICAL_REPAIR_NUDGE_TEXT
        # The nudge kind stamp is "repair" (new shape).
        assert (
            nudge.additional_kwargs["attestation_nudge_kind"]
            == "repair"
        )
        # The repair-nudge-sent flag flips True.
        assert result[ATTESTATION_REPAIR_NUDGE_SENT_KEY] is True
        # The next-turn streak resets (a fresh episode starts at 0).
        # The repair-nudge is itself a checkpoint-durable HumanMessage,
        # so the leader's NEXT turn will see it as the prior message
        # and may break the streak by replying differently.
        assert (
            result[ATTESTATION_LAST_BARE_AI_CONTENT_KEY] is None
        )

    def test_fourth_identical_after_repair_loud_terminal(
        self, monkeypatch, caplog
    ):
        """4th identical bare reply AFTER the repair nudge fires:
        the leader ignored the repair — guard second-strike ⇒
        loud TERMINAL_AFTER_BOUND with the standard escalation
        machinery."""
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, ledger = _make_gate_node(
            instance_id="p2-fourth-it",
            denied_count_getter=lambda: 0,
        )
        # Pre-stage: 2 prior identical "🔇" replies + repair
        # already sent. The current FINAL is another identical
        # "🔇" (no tool calls between consecutive identicals).
        state = {
            "messages": [
                HumanMessage(content="please do it"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "send_message",
                            "args": {"target": "child-id"},
                            "id": "c1",
                        }
                    ],
                ),
                AIMessage(content="🔇", id="m-1"),
                AIMessage(content="🔇", id="m-2"),
                AIMessage(content="🔇", id="m-3"),
            ],
            ATTESTATION_CONSECUTIVE_IDENTICAL_KEY: 2,
            ATTESTATION_LAST_BARE_AI_CONTENT_KEY: "🔇",
            ATTESTATION_REPAIR_NUDGE_SENT_KEY: True,
        }

        with caplog.at_level("INFO", logger="daemon.graph"):
            result = asyncio.run(
                node(
                    state,
                    config={
                        "configurable": {"thread_id": "p2-fourth-it"}
                    },
                )
            )

        # Loud terminal — decision is TERMINAL_AFTER_BOUND.
        assert result["attestation_route"] is None
        # Standard escalation machinery wrote the flag.
        ledger.set_escalated_and_reset.assert_called_once()
        # Counter resets on terminal.
        assert (
            result[ATTESTATION_CONSECUTIVE_IDENTICAL_KEY] == 0
        )
        assert (
            result[ATTESTATION_REPAIR_NUDGE_SENT_KEY] is False
        )
        # Greppable audit rows — the P2 second-strike row AND the
        # canonical terminal row both fire.
        log_text = "\n".join(rec.getMessage() for rec in caplog.records)
        assert (
            "event=leader_completion_gate_consecutive_identical_terminal"
            in log_text
        )
        assert (
            "event=leader_completion_gate_terminal_after_bound"
            in log_text
        )

    def test_tool_call_interleaved_resets_streak(
        self, monkeypatch, caplog
    ):
        """A leader that interleaves a tool call between identical
        bare replies is doing real work — the streak resets,
        the guard does NOT fire.

        False-positive case (task constraint): legitimate concise
        finals OUTSIDE deny-loop; repeated short acks WITH tool
        calls interleaved.
        """
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, _ledger = _make_gate_node(
            instance_id="p2-toolcall-it",
            denied_count_getter=lambda: 0,
        )
        # Pre-stage the streak at 2 with a tool-call interleaved
        # before the FINAL AIMessage. The current FINAL is bare
        # (tool-call messages are not the LAST AI), but the tool
        # call above it breaks the streak.
        state = {
            "messages": [
                HumanMessage(content="do work"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "send_message",
                            "args": {"target": "child-2"},
                            "id": "dispatch-2",
                        }
                    ],
                ),
                AIMessage(content="Done."),
            ],
            ATTESTATION_CONSECUTIVE_IDENTICAL_KEY: 2,
            ATTESTATION_LAST_BARE_AI_CONTENT_KEY: "Done.",
            ATTESTATION_REPAIR_NUDGE_SENT_KEY: False,
        }
        result = asyncio.run(
            node(
                state,
                config={"configurable": {"thread_id": "p2-toolcall-it"}},
            )
        )
        # Streak resets — the FINAL AIMessage is identical to the
        # prior bare, but the message ABOVE it carried a tool call.
        # The streak counter resets to 1 (a new streak starts with
        # this bare reply as its first data point).
        assert (
            result[ATTESTATION_CONSECUTIVE_IDENTICAL_KEY] == 1
        )
        # The repair body did NOT fire — the standard nudge is
        # still in play (count 1 < threshold 3).
        nudge = result["messages"][0]
        assert nudge.content == ATTESTATION_NUDGE_TEXT

    def test_different_content_resets_streak(
        self, monkeypatch, caplog
    ):
        """A leader that varies its bare reply resets the streak
        (real variation, not mute-wall)."""
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, _ledger = _make_gate_node(
            instance_id="p2-diff-it",
            denied_count_getter=lambda: 0,
        )
        state = _delegated_without_attest_messages("Different reply")
        state[ATTESTATION_CONSECUTIVE_IDENTICAL_KEY] = 5
        state[ATTESTATION_LAST_BARE_AI_CONTENT_KEY] = "Done."
        state[ATTESTATION_REPAIR_NUDGE_SENT_KEY] = False
        result = asyncio.run(
            node(
                state,
                config={"configurable": {"thread_id": "p2-diff-it"}},
            )
        )
        # Streak resets to 1 (different content starts a new streak).
        assert (
            result[ATTESTATION_CONSECUTIVE_IDENTICAL_KEY] == 1
        )
        assert (
            result[ATTESTATION_LAST_BARE_AI_CONTENT_KEY]
            == "Different reply"
        )
        # The repair body did NOT fire.
        nudge = result["messages"][0]
        assert nudge.content == ATTESTATION_NUDGE_TEXT

    def test_whitespace_normalization_catches_trailing_newline_variant(
        self, monkeypatch, caplog
    ):
        """The P2 normalization collapses trivial whitespace
        variants — a leader that adds a trailing newline does NOT
        dodge the guard."""
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, _ledger = _make_gate_node(
            instance_id="p2-ws-it",
            denied_count_getter=lambda: 0,
        )
        # Pre-stage: 2 prior identical "🔇" replies. Current
        # FINAL is "🔇\n\n  " (whitespace variant) — the
        # normalization collapses it to "🔇" so the streak
        # increments to 3.
        state = {
            "messages": [
                HumanMessage(content="please do it"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "send_message",
                            "args": {"target": "child-id"},
                            "id": "c1",
                        }
                    ],
                ),
                AIMessage(content="🔇", id="m-1"),
                AIMessage(content="🔇", id="m-2"),
                AIMessage(content="🔇\n\n  ", id="m-3"),
            ],
            ATTESTATION_CONSECUTIVE_IDENTICAL_KEY: 2,
            ATTESTATION_LAST_BARE_AI_CONTENT_KEY: "🔇",
            ATTESTATION_REPAIR_NUDGE_SENT_KEY: False,
        }
        result = asyncio.run(
            node(
                state,
                config={"configurable": {"thread_id": "p2-ws-it"}},
            )
        )
        # Repair body fires (counter crossed threshold).
        nudge = result["messages"][0]
        assert nudge.content == ATTESTATION_IDENTICAL_REPAIR_NUDGE_TEXT


# ─────────────────────────────────────────────────────────────────────────────
# (E) P3 — degenerate-shape guard
# ─────────────────────────────────────────────────────────────────────────────


class TestDegenerateShapeGuard:
    """P3 degenerate-shape guard (gradient family catcher).

    Scenarios:
    * degenerate finals inside a deny-loop ⇒ streak increments
    * non-degenerate reply (real prose, or any reply with tool
      calls) resets the streak
    * 3rd degenerate final ⇒ anti-silence retry (audit row +
      standard nudge — the anti-silence instruction rides the
      existing DENIED branch)
    * 6th degenerate final (second streak crossing) ⇒ loud
      TERMINAL_AFTER_BOUND
    * degenerate replies OUTSIDE a deny-loop (denied_count == 0)
      do NOT trigger the guard (legit concise finals are NOT a
      gate-withhold event)
    """

    def test_emoji_only_deny_loop_emits_anti_silence_audit(
        self, monkeypatch, caplog
    ):
        """Three emoji-only finals inside a deny-loop: anti-silence
        audit row fires (P3 first-strike)."""
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        # denied_count=2 ⇒ already in a deny-loop.
        node, _manager, _ledger = _make_gate_node(
            instance_id="p3-emoji-it",
            denied_count_getter=lambda: 2,
        )
        # Pre-stage: 2 prior emoji-only finals + degenerate streak 2.
        state = _delegated_without_attest_messages("🔇")
        state[ATTESTATION_DEGENERATE_STREAK_KEY] = 2
        with caplog.at_level("INFO", logger="daemon.graph"):
            result = asyncio.run(
                node(
                    state,
                    config={
                        "configurable": {"thread_id": "p3-emoji-it"}
                    },
                )
            )
        log_text = "\n".join(rec.getMessage() for rec in caplog.records)
        assert (
            "event=leader_completion_gate_degenerate_retry" in log_text
        )
        # Streak at 3 — crosses the threshold.
        assert result[ATTESTATION_DEGENERATE_STREAK_KEY] == 3

    def test_sixth_degenerate_loud_terminal(
        self, monkeypatch, caplog
    ):
        """6th degenerate final (second streak crossing) ⇒ loud
        TERMINAL_AFTER_BOUND via the P3 second-strike arm."""
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, ledger = _make_gate_node(
            instance_id="p3-sixth-it",
            denied_count_getter=lambda: 2,
        )
        # Pre-stage degenerate streak at 5 (one short of the
        # second threshold). Final AIMessage is emoji-only ⇒
        # streak goes 5 → 6 ⇒ second-strike loud terminal.
        state = _delegated_without_attest_messages("🔇")
        state[ATTESTATION_DEGENERATE_STREAK_KEY] = 5
        with caplog.at_level("INFO", logger="daemon.graph"):
            result = asyncio.run(
                node(
                    state,
                    config={
                        "configurable": {"thread_id": "p3-sixth-it"}
                    },
                )
            )
        assert result["attestation_route"] is None
        ledger.set_escalated_and_reset.assert_called_once()
        log_text = "\n".join(rec.getMessage() for rec in caplog.records)
        assert (
            "event=leader_completion_gate_degenerate_terminal" in log_text
        )
        assert (
            "event=leader_completion_gate_terminal_after_bound"
            in log_text
        )
        # Streak resets on terminal.
        assert result[ATTESTATION_DEGENERATE_STREAK_KEY] == 0

    def test_degenerate_outside_deny_loop_does_not_count(
        self, monkeypatch, caplog
    ):
        """P3 is scoped to an active deny-loop only (denied_count >
        0). A single emoji reply OUTSIDE a deny-loop (legit concise
        final on an unrequired path) is NOT a P3 event."""
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        # denied_count=0 ⇒ NOT in a deny-loop.
        node, _manager, _ledger = _make_gate_node(
            instance_id="p3-outside-it",
            denied_count_getter=lambda: 0,
        )
        state = _delegated_without_attest_messages("🔇")
        with caplog.at_level("INFO", logger="daemon.graph"):
            result = asyncio.run(
                node(
                    state,
                    config={
                        "configurable": {"thread_id": "p3-outside-it"}
                    },
                )
            )
        # Streak stays at 0 — the P3 guard does NOT fire outside
        # a deny-loop.
        assert result[ATTESTATION_DEGENERATE_STREAK_KEY] == 0
        log_text = "\n".join(rec.getMessage() for rec in caplog.records)
        assert (
            "event=leader_completion_gate_degenerate_retry" not in log_text
        )

    def test_long_prose_resets_degenerate_streak(
        self, monkeypatch, caplog
    ):
        """A non-degenerate reply (real prose) resets the P3
        streak — the gradient-family incident pattern was the
        content gradually shrinking (each reply shorter than
        the prior), but the guard stays armed as long as every
        reply is degenerate. A real prose reply between
        degenerate ones resets the streak."""
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, _ledger = _make_gate_node(
            instance_id="p3-prose-it",
            denied_count_getter=lambda: 2,
        )
        # Final reply is real prose (4 words — above the 3-word
        # threshold) ⇒ not degenerate ⇒ streak resets.
        state = _delegated_without_attest_messages(
            "All four patches shipped."
        )
        state[ATTESTATION_DEGENERATE_STREAK_KEY] = 5
        result = asyncio.run(
            node(
                state,
                config={"configurable": {"thread_id": "p3-prose-it"}},
            )
        )
        assert result[ATTESTATION_DEGENERATE_STREAK_KEY] == 0


# ─────────────────────────────────────────────────────────────────────────────
# (F) P4 — observability — persisted state + cross-turn visibility
# ─────────────────────────────────────────────────────────────────────────────


class TestObservabilityPersistedState:
    """P4 — the new channels MUST persist across turns (the
    incident's counter accumulated 24 denials before the fatal turn
    — the state MUST survive subsequent turns).

    Also: every guard's audit row is greppable via ``event=`` so
    operators can see WHY the gate fired BEFORE a mute wall
    emerges (watchers see the guard trigger; without P4 the gate
    only surfaces the deny+nudge cycle in the daemon log, not the
    guard reasoning).
    """

    def test_withhold_counter_persists_across_normal_denies(
        self, monkeypatch, caplog
    ):
        """Normal deny path (no composition-gate conversion):
        the withhold counter carries the prior value forward.
        """
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, _ledger = _make_gate_node(
            instance_id="p4-withhold-it",
            denied_count_getter=lambda: 1,  # bound not reached
        )
        # Pre-stage the withhold counter at 3 (a prior
        # composition-gate conversion set it).
        state = _delegated_without_attest_messages("Working on it.")
        state[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] = 3

        result = asyncio.run(
            node(
                state,
                config={
                    "configurable": {"thread_id": "p4-withhold-it"}
                },
            )
        )
        # The withhold counter carries forward (3 → 3, no
        # composition-gate conversion this turn). The
        # next-deny evaluation sees the accumulated budget
        # state.
        assert (
            result[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] == 3
        )

    def test_attested_allow_resets_withhold_counter(
        self, monkeypatch, caplog
    ):
        """Attested allow (the leader delivered a real report):
        the withhold counter resets — a fresh mission episode
        starts from zero on every guard."""
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        # Attested allow path — the FINAL AIMessage is a long
        # standalone report (no tool calls, ≥ 150 words).
        long_report = (
            "All four patches landed and shipped to the integration "
            "branch. Patch 1 fixed the off-by-one in the cache TTL "
            "calculator; the unit tests now exercise both the "
            "elapsed-second and wall-clock-second boundaries at the "
            "second and minute granularity. Patch 2 cleaned up the "
            "dead imports in the worker pool module after the "
            "migration, removing the legacy compatibility shim and the "
            "related test scaffolding. Patch 3 refactored the "
            "error-reporting decorator so the stack-frame metadata is "
            "consistent across all four call sites in the graph node "
            "and the manager facade. Patch 4 added the missing "
            "operator-boot log line for the new resolver module so "
            "operators can grep the boot summary for the resolved "
            "effective values including the mode, window, bound, and "
            "gate locations active at the time. All four patches "
            "passed their respective suites on the first run with no "
            "flake; the integration matrix is green end-to-end across "
            "all environments we maintain. No follow-ups outstanding; "
            "the mission is complete and ready for review. The release "
            "notes draft is staged on the docs branch with the "
            "per-patch rationale paragraphs and the cross-references to "
            "the upstream incident reports; the FE mirror was verified "
            "and the build artifact attached to the rollout ticket for "
            "traceability."
        )
        node, _manager, _ledger = _make_gate_node(
            instance_id="p4-reset-it",
            denied_count_getter=lambda: 5,
        )
        # Pre-stage the guards' channels at non-zero values.
        state = {
            "messages": [
                HumanMessage(content="do it"),
                AIMessage(content="", tool_calls=[
                    {"name": "send_message", "args": {"target": "child"}, "id": "d1"},
                ]),
                AIMessage(content="", tool_calls=[
                    {"name": "attest_completion", "args": {}, "id": "a1"},
                ]),
                AIMessage(content=long_report),
            ],
            ATTESTATION_WITHHOLD_DENY_COUNT_KEY: 5,
            ATTESTATION_CONSECUTIVE_IDENTICAL_KEY: 3,
            ATTESTATION_REPAIR_NUDGE_SENT_KEY: True,
            ATTESTATION_DEGENERATE_STREAK_KEY: 4,
        }
        result = asyncio.run(
            node(
                state,
                config={"configurable": {"thread_id": "p4-reset-it"}},
            )
        )
        # Attested allow path — every guard channel resets.
        assert (
            result[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] == 0
        )
        assert (
            result[ATTESTATION_CONSECUTIVE_IDENTICAL_KEY] == 0
        )
        assert (
            result[ATTESTATION_REPAIR_NUDGE_SENT_KEY] is False
        )
        assert (
            result[ATTESTATION_DEGENERATE_STREAK_KEY] == 0
        )


# ─────────────────────────────────────────────────────────────────────────────
# (G) FALSE-POSITIVE GUARDS — task constraint
# ─────────────────────────────────────────────────────────────────────────────


class TestFalsePositiveGuards:
    """Task constraint: zero behavioral change when the gate is
    healthy. The new guards MUST be inert outside deny-loop /
    degenerate / consecutive-identical conditions.
    """

    def test_legit_concise_final_outside_deny_loop_passes(
        self, monkeypatch, caplog
    ):
        """A leader that emits a short non-degenerate reply on
        the ALLOW path (no prior deny) is NOT a P3 event — the
        guard only fires inside a deny-loop."""
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, _ledger = _make_gate_node(
            instance_id="fp-concise-it",
            denied_count_getter=lambda: 0,  # not in deny-loop
        )
        # Single short reply (2 words — degenerate shape, but
        # outside deny-loop).
        state = _delegated_without_attest_messages("Done.")
        result = asyncio.run(
            node(
                state,
                config={"configurable": {"thread_id": "fp-concise-it"}},
            )
        )
        # Degenerate streak stays at 0 — the guard does not fire.
        assert result[ATTESTATION_DEGENERATE_STREAK_KEY] == 0
        # Consecutive counter goes to 1 (first bare reply) — no
        # guard fire.
        assert result[ATTESTATION_CONSECUTIVE_IDENTICAL_KEY] == 1

    def test_legit_multi_deny_cycle_completes(
        self, monkeypatch, caplog
    ):
        """A legitimate multi-deny cycle (real work between
        denies) completes normally — the P1 budget fires ONLY
        on never-spoke arcs."""
        # Real judge returning "not_complete" stamps substantive.
        async def _judge_not_complete(
            config, user_payload, *, timeout_s, system_prompt=None
        ):
            return (
                '{"verdict": "not_complete", "evidence_cited": [], '
                '"advisory_note_text": "", "rationale": "short status"}',
                "fake-quick",
            )

        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_not_complete
        )
        # Pre-stage at the bound (3) so the NEXT evaluation hits
        # the exhaustion path with substantive=True ⇒ terminal
        # stands (the normal v3 ruling).
        node, _manager, ledger = _make_gate_node(
            instance_id="fp-multi-deny-it",
            denied_count_getter=lambda: 3,
        )
        state = _delegated_without_attest_messages(
            "Status update: working on it."
        )
        state[ATTESTATION_ANY_SUBSTANTIVE_KEY] = True
        result = asyncio.run(
            node(
                state,
                config={
                    "configurable": {"thread_id": "fp-multi-deny-it"}
                },
            )
        )
        # Judge-spoke path ⇒ TERMINAL_AFTER_BOUND stands, normal
        # loud-exit machinery fires.
        assert result["attestation_route"] is None
        ledger.set_escalated_and_reset.assert_called_once()
        # Withhold counter was NOT incremented — substantive=True
        # short-circuits the composition gate.
        assert (
            result[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] == 0
        )


# ─────────────────────────────────────────────────────────────────────────────
# (H) NUDGE TEXT BYTE-PIN — single source of truth
# ─────────────────────────────────────────────────────────────────────────────


class TestNudgeTextBytePin:
    """The P2 repair-nudge body is the canonical literal anchor
    (single home). Any byte change fails this test and forces a
    conscious review of the nudge text — mirroring the
    ``test_attestation_nudge_text_canonical_byte_pin`` discipline.
    """

    def test_repair_nudge_text_canonical_byte_pin(self):
        """The full-literal body of ``ATTESTATION_IDENTICAL_REPAIR_
        NUDGE_TEXT`` — any byte change fails this test and forces
        a deliberate update in lockstep with the constant.

        The body is intentionally short and action-oriented —
        a leader that hit the loop is unlikely to read a long
        nudge; this asks for ONE concrete action.
        """
        expected = (
            "[Attestation Gate — Repair Nudge] Your last 3 replies "
            "were identical and carried no tool calls. The gate is "
            "in a withhold loop on this mission and will not respond "
            "to additional copies. Choose exactly one now: (1) call "
            "attest_completion ALONE in a pure toolcall turn (empty "
            "content) then deliver your final report as a separate "
            "standalone message; (2) dispatch or finish the remaining "
            "work via send_message; (3) ask the user a direct "
            "question. Repeating this same reply will end the mission "
            "loudly as COMPLETED-UNVERIFIED (gate escalated)."
        )
        assert ATTESTATION_IDENTICAL_REPAIR_NUDGE_TEXT == expected
        # Structural guards so a bad literal can't silently equal
        # via empty / None tricks.
        assert expected.startswith("[Attestation Gate — Repair Nudge]")
        assert expected.rstrip().endswith("gate escalated).")