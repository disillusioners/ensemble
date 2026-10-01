"""Unit tests for the completion-gate bounds fix (P1-P4).

Closing DEFECT critical-note ``f018da70`` — the attestation gate's
'teminal-withheld' epoch (judge_invoked=False) is an UNBOUNDED deny
loop. P1-P4 add per-dispatch bounds + content-shape guards + nudge
persistence so a leader mission cannot run-away mute.

Each guard lives in production code with a single canonical
counterpart (no double bookkeeping, no per-test stubbing):

* P1 — ``daemon.services.attestation_gate.withhold_budget_exhausted``
  + ``WITHHOLD_DENY_BUDGET_DEFAULT`` (the single runtime home;
  ``daemon.graph.WITHHOLD_DENY_BUDGET`` is a lazy compat alias
  resolved by that module's ``__getattr__``). Composition
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
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.graph import (
    ATTESTATION_ANY_SUBSTANTIVE_KEY,
    ATTESTATION_CONSECUTIVE_IDENTICAL_KEY,
    ATTESTATION_DEGENERATE_STREAK_KEY,
    ATTESTATION_DEGENERATE_RETRY_NUDGE_TEXT,
    ATTESTATION_DENY_PROGRESS_KEY,
    ATTESTATION_FRESH_EPISODE_CONSUMED_ID_KEY,
    ATTESTATION_IDENTICAL_REPAIR_NUDGE_TEXT,
    ATTESTATION_LAST_BARE_AI_CONTENT_KEY,
    ATTESTATION_NUDGE_TEXT,
    ATTESTATION_REPAIR_NUDGE_SENT_KEY,
    ATTESTATION_RUN_DENY_CAP,
    ATTESTATION_RUN_DENY_COUNT_KEY,
    ATTESTATION_WITHHOLD_DENY_COUNT_KEY,
    CONSECUTIVE_IDENTICAL_THRESHOLD,
    DEGENERATE_STREAK_THRESHOLD,
    DEGENERATE_WORD_THRESHOLD,
    WITHHOLD_DENY_BUDGET,
    _is_degenerate_shape,
    _normalize_bare_content,
    create_attestation_gate_node,
    should_end_attestation,
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
from daemon.config import LimitsConfig
from langgraph.graph import END as GRAPH_END


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
        # Round-2 tidier: ``daemon.graph.WITHHOLD_DENY_BUDGET`` is a
        # lazy compat alias (module ``__getattr__``) resolving to the
        # true home — this pin asserts the alias wiring stays intact.
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
    * 3rd degenerate final ⇒ anti-silence RETRY AUDIT ROW; the
      nudge that ships is the STANDARD/DIRECTIVE deny nudge (the
      dedicated anti-silence body is canonical-but-unwired —
      round-3 F2 disposition, wiring ledgered)
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
        retry AUDIT ROW fires (P3 first-strike). The shipped nudge
        is the standard/directive body — the dedicated anti-silence
        text is unwired (ledgered), so this test pins the row, not
        a special body."""
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
        # Round-3 F2 pin: the SHIPPED nudge is the standard/directive
        # deny body — NOT the canonical-but-unwired anti-silence text.
        nudge = result["messages"][-1]
        assert (
            nudge.content != ATTESTATION_DEGENERATE_RETRY_NUDGE_TEXT
        )
        assert not nudge.content.startswith(
            "[Attestation Gate — Anti-Silence]"
        )
        assert nudge.additional_kwargs["attestation_nudge_kind"] in (
            "standard",
            "directive",
        )

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

# ─────────────────────────────────────────────────────────────────────────────
# (G) CEREMONY-ARM COVERAGE (2026-10-01, incident 6f961c43) + b3 RUN-DENY CAP
# ─────────────────────────────────────────────────────────────────────────────
#
# The staged P1-P4 tests were authored against the MUTE arm
# (34978dfc — bare-emoji repeats). The TWIN incident (6f961c43 —
# leader 'Promote v0.16.8', job cb89e01d) is the CEREMONY arm: the
# leader awaiting a user nonce echo sent SHORT-BUT-SUBSTANTIVE
# replies ('In-hand (fresh) — unchanged… Holding…') interleaved with
# legitimate child-status polls. The shape EVADES P2 (interleaved
# tool calls reset the consecutive-identical streak) and P3
# (4-5 words > DEGENERATE_WORD_THRESHOLD) — so the bound must come
# from the P1 withhold budget (judge never spoke) and, behind it,
# the b3 per-mission run-deny cap. These tests pin that.


def _poll_ai_message(call_id: str) -> AIMessage:
    """A legitimate child-status poll tool call (the incident's
    poll accelerant — subtree_status)."""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "subtree_status",
                "args": {"instance_id": "child-x"},
                "id": call_id,
            }
        ],
    )


def _drive_gate_loop(
    node, state, *, reply_text: str, max_calls: int = 60
) -> tuple[list[dict], dict]:
    """Drive the gate node in a feedback loop — the incident shape.

    Each deny result's channel writes are merged back into ``state``
    (simulating checkpoint persistence — which in production works
    via the SessionState channel declarations pinned by
    ``TestGateChannelsDeclaredOnSessionState``), the nudge message is
    appended, and the leader's next turn is appended as
    ``[poll AI, ToolMessage, short reply AI]`` — the ceremony-arm
    turn shape (poll child status, then hold).

    Returns ``(deny_results, final_result)``.
    """
    from langchain_core.messages import ToolMessage

    deny_results: list[dict] = []
    for i in range(max_calls):
        result = asyncio.run(
            node(state, config={"configurable": {"thread_id": "loop-it"}})
        )
        if result.get("attestation_route") != "agent":
            return deny_results, result
        deny_results.append(result)
        # Merge channel writes (checkpoint persistence simulation).
        for key, value in result.items():
            if key != "messages":
                state[key] = value
        state["messages"].extend(result.get("messages", []))
        # The leader's next turn: poll child status, then hold.
        state["messages"].append(_poll_ai_message(f"poll-{i}"))
        state["messages"].append(
            ToolMessage(content="children: idle", tool_call_id=f"poll-{i}")
        )
        state["messages"].append(AIMessage(content=reply_text))
    raise AssertionError(
        f"gate loop did not terminate within {max_calls} evaluations"
    )


class TestGateChannelsDeclaredOnSessionState:
    """The compiled-graph channel-declaration pin (2026-10-01).

    The staged fix/completion-gate-bounds branch wrote five gate
    counter channels from the gate node WITHOUT declaring them on
    ``SessionState`` — and langgraph 1.0.9 SILENTLY DROPS undeclared
    keys from node updates, so every counter write was discarded at
    the first channel merge and the P1 budget could never accumulate
    in the compiled leader graph. Node-level unit tests (all the
    classes above) invoke the node function directly and CANNOT see
    this — hence this compiled-graph canary + the annotations pin.
    """

    def test_all_gate_counter_channels_declared(self, real_graph_module):
        """Every channel the gate node writes is a declared
        SessionState field (exact string match on the KEY constants).

        Uses the ``real_graph_module`` fixture: the root test
        conftest stubs langgraph with MagicMocks, so the REAL
        SessionState (a real TypedDict subclass) is only
        introspectable inside the eviction window.
        """
        for key in (
            ATTESTATION_ANY_SUBSTANTIVE_KEY,
            ATTESTATION_WITHHOLD_DENY_COUNT_KEY,
            ATTESTATION_CONSECUTIVE_IDENTICAL_KEY,
            ATTESTATION_LAST_BARE_AI_CONTENT_KEY,
            ATTESTATION_REPAIR_NUDGE_SENT_KEY,
            ATTESTATION_DEGENERATE_STREAK_KEY,
            ATTESTATION_RUN_DENY_COUNT_KEY,
            ATTESTATION_FRESH_EPISODE_CONSUMED_ID_KEY,
        ):
            assert key in real_graph_module.SessionState.__annotations__, (
                f"gate channel {key!r} is written by the gate node but "
                "not declared on SessionState — langgraph would "
                "silently drop every write"
            )

    def test_compiled_graph_preserves_counter_writes(
        self, real_graph_module
    ):
        """Compiled-graph canary: a SessionState graph node writing
        all gate counter keys merges them into the next state —
        the silent-drop regression pin (reproduced against langgraph
        1.0.9 before the declarations landed)."""
        # Real langgraph imports INSIDE the eviction window.
        from langgraph.graph import StateGraph
        from langgraph.graph import END as REAL_END
        from langgraph.graph import START as REAL_START

        gs = real_graph_module.SessionState

        def writer_node(state):
            return {
                ATTESTATION_WITHHOLD_DENY_COUNT_KEY: 7,
                ATTESTATION_RUN_DENY_COUNT_KEY: 3,
                ATTESTATION_CONSECUTIVE_IDENTICAL_KEY: 2,
                ATTESTATION_DEGENERATE_STREAK_KEY: 1,
                ATTESTATION_REPAIR_NUDGE_SENT_KEY: True,
                ATTESTATION_LAST_BARE_AI_CONTENT_KEY: "🔇",
                ATTESTATION_FRESH_EPISODE_CONSUMED_ID_KEY: "msg-1",
            }

        graph = StateGraph(gs)
        graph.add_node("writer", writer_node)
        graph.add_edge(REAL_START, "writer")
        graph.add_edge("writer", REAL_END)
        app = graph.compile()

        out = app.invoke({"messages": []})
        assert out[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] == 7
        assert out[ATTESTATION_RUN_DENY_COUNT_KEY] == 3
        assert out[ATTESTATION_CONSECUTIVE_IDENTICAL_KEY] == 2
        assert out[ATTESTATION_DEGENERATE_STREAK_KEY] == 1
        assert out[ATTESTATION_REPAIR_NUDGE_SENT_KEY] is True
        assert out[ATTESTATION_LAST_BARE_AI_CONTENT_KEY] == "🔇"
        assert out[ATTESTATION_FRESH_EPISODE_CONSUMED_ID_KEY] == "msg-1"

    def test_sentinel_consumption_is_one_shot(self):
        """The fresh-episode sentinel PERSISTS in the checkpointed
        history, so presence-based detection re-fired the channel
        reset on EVERY evaluation — silently zeroing the counters
        after each write (bounds could never accumulate
        post-revival). The consumption marker makes it one-shot per
        sentinel message id: the SECOND evaluation with the same
        sentinel in history must NOT re-reset.

        2026-10-01 ordering guarantee: the reset is applied to the
        state the body READS (pre-body), so the first post-revival
        evaluation's deny consumes slot 1 of the FRESH episode's
        budget — not the stale one.
        """
        sentinel = HumanMessage(
            content="resume the mission",
            id="sentinel-msg-1",
            additional_kwargs={"fresh_episode_attestation_reset": True},
        )
        state = _delegated_without_attest_messages("Still holding.")
        state["messages"].insert(0, sentinel)
        state[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] = 5
        state[ATTESTATION_RUN_DENY_COUNT_KEY] = 9

        node, _manager, _ledger = _make_gate_node(
            instance_id="sentinel-once-it",
            denied_count_getter=lambda: 1,  # bound NOT exhausted — plain deny
        )

        # Evaluation 1 — the fresh-episode reset fired BEFORE the
        # body read: stale withhold 5 / run 9 are invisible; the
        # deny runs on the fresh counters (run 0 → 1) and the
        # consumption marker is stamped.
        result1 = asyncio.run(
            node(
                state,
                config={
                    "configurable": {"thread_id": "sentinel-once-it"}
                },
            )
        )
        assert result1["attestation_route"] == "agent"
        assert result1[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] == 0
        assert result1[ATTESTATION_RUN_DENY_COUNT_KEY] == 1
        assert (
            result1[ATTESTATION_FRESH_EPISODE_CONSUMED_ID_KEY]
            == "sentinel-msg-1"
        )

        # Evaluation 2 — same sentinel STILL in history, but already
        # consumed: no re-reset, the deny increments the counters.
        state.update(
            {k: v for k, v in result1.items() if k != "messages"}
        )
        state["messages"].extend(result1.get("messages", []))
        result2 = asyncio.run(
            node(
                state,
                config={
                    "configurable": {"thread_id": "sentinel-once-it"}
                },
            )
        )
        assert result2["attestation_route"] == "agent"
        assert result2[ATTESTATION_RUN_DENY_COUNT_KEY] == 2


class TestCeremonyArmCoverage:
    """Commission cases (a) / (b) / (c) — the 6f961c43 ceremony arm.

    (a) short-but-substantive replies denied in a bounded epoch —
        the P1 withhold budget is the operative bound (P2/P3 are
        evaded by construction); the loop terminates LOUD.
    (b) budget exhaustion terminates WITHOUT a silent unattested
        exit — the standard escalation machinery writes
        COMPLETED-UNVERIFIED (gate escalated).
    (c) recovery-commission restart semantics — per-dispatch budget
        safety: fresh user-dispatched episode = fresh bounded budget;
        mid-loop resume without a user message carries the budget.
    """

    CEREMONY_REPLY = "In-hand (fresh) — unchanged. Holding."

    def _ceremony_state(self) -> dict:
        return _delegated_without_attest_messages(self.CEREMONY_REPLY)

    def test_shape_evades_p2_and_p3_guards(self, monkeypatch, caplog):
        """PIN the evasion: the ceremony-arm turn shape (poll tool
        call interleaved + 5-word reply) must NOT trip the
        consecutive-identical or degenerate guards — proving the
        bound in the following tests comes from the withhold
        budget / run cap, not the content guards."""
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, _ledger = _make_gate_node(
            instance_id="ceremony-evade-it",
            denied_count_getter=lambda: 1,  # bound NOT exhausted — plain deny
        )
        state = self._ceremony_state()
        state["messages"].append(_poll_ai_message("poll-0"))
        from langchain_core.messages import ToolMessage

        state["messages"].append(
            ToolMessage(content="children: idle", tool_call_id="poll-0")
        )
        state["messages"].append(AIMessage(content=self.CEREMONY_REPLY))

        with caplog.at_level("INFO", logger="daemon.graph"):
            result = asyncio.run(
                node(
                    state,
                    config={
                        "configurable": {"thread_id": "ceremony-evade-it"}
                    },
                )
            )
        assert result["attestation_route"] == "agent"
        # Content guards inert on this shape.
        assert result[ATTESTATION_CONSECUTIVE_IDENTICAL_KEY] == 1  # restarts, no fire
        assert result[ATTESTATION_DEGENERATE_STREAK_KEY] == 0
        log_text = "\n".join(r.getMessage() for r in caplog.records)
        assert "consecutive_identical_nudge" not in log_text
        assert "degenerate_retry" not in log_text
        assert "consecutive_identical_terminal" not in log_text

    def test_a_short_substantive_loop_bounded_by_withhold_budget(
        self, monkeypatch, caplog
    ):
        """(a) The full ceremony-arm loop: short-but-substantive
        replies + legit polls, judge never speaks, deny bound
        exhausted every evaluation. The loop MUST terminate via the
        P1 withhold budget at budget+1 evaluations — LOUD, with the
        escalation machinery — not run to recursion_limit."""
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, ledger = _make_gate_node(
            instance_id="ceremony-loop-it",
            denied_count_getter=lambda: 3,  # bound exhausted every eval
        )
        state = self._ceremony_state()

        with caplog.at_level("INFO", logger="daemon.graph"):
            denies, final = _drive_gate_loop(
                node, state, reply_text=self.CEREMONY_REPLY
            )

        # Bounded: exactly budget+1 evaluations (8 denies, 9th loud).
        assert len(denies) == WITHHOLD_DENY_BUDGET
        assert final["attestation_route"] is None
        # The withhold counter climbed 0→8 across the denies.
        assert (
            denies[-1][ATTESTATION_WITHHOLD_DENY_COUNT_KEY]
            == WITHHOLD_DENY_BUDGET
        )
        # Loud exit — the standard escalation machinery wrote.
        ledger.set_escalated_and_reset.assert_called_once()
        # Every deny in the arc carried a nudge (never a silent
        # evaluation).
        assert all("messages" in d for d in denies)
        log_text = "\n".join(rec.getMessage() for rec in caplog.records)
        # P1's operative rows fired; the content guards stayed out.
        assert (
            "event=leader_completion_gate_bound_exhausted_never_spoke"
            in log_text
        )
        assert "event=leader_completion_gate_withholding_exit" in log_text
        assert "event=leader_completion_gate_terminal_after_bound" in log_text
        assert "consecutive_identical_terminal" not in log_text
        assert "degenerate_terminal" not in log_text

    def test_b_budget_exhaustion_is_loud_not_silent(
        self, monkeypatch, caplog
    ):
        """(b) At budget exhaustion the exit is LOUD: the escalation
        write lands (COMPLETED-UNVERIFIED surface), the audit rows
        fire, and — the not-silent pins — NO nudge is injected on
        the exit path (no 'messages' key) and the counters RESET so
        a fresh episode starts bounded-from-zero, not pre-poisoned."""
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, ledger = _make_gate_node(
            instance_id="ceremony-loud-it",
            denied_count_getter=lambda: 3,
        )
        state = self._ceremony_state()
        state[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] = (
            WITHHOLD_DENY_BUDGET
        )
        state[ATTESTATION_RUN_DENY_COUNT_KEY] = 4

        with caplog.at_level("INFO", logger="daemon.graph"):
            result = asyncio.run(
                node(
                    state,
                    config={
                        "configurable": {"thread_id": "ceremony-loud-it"}
                    },
                )
            )

        assert result["attestation_route"] is None
        # NOT silent: the loud write happened.
        ledger.set_escalated_and_reset.assert_called_once()
        # NOT a deny: nothing is injected on the exit path.
        assert "messages" not in result
        # A fresh episode starts from zero on every counter.
        assert result[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] == 0
        assert result[ATTESTATION_RUN_DENY_COUNT_KEY] == 0
        log_text = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "event=leader_completion_gate_withholding_exit" in log_text
        assert (
            "completion_gate_escalated=true" in log_text
        )

    def test_c_recovery_restart_budget_semantics(
        self, monkeypatch, caplog
    ):
        """(c) Per-dispatch budget safety under recovery restarts.

        A recovery commission that re-dispatches the leader WITH a
        fresh user message (sentinel) starts a NEW bounded episode:
        counters reset once, then ACCUMULATE again within the new
        episode (the one-shot consumption marker prevents the
        per-evaluation wipe). A revival WITHOUT a fresh user message
        (mid-loop resume) carries the budget — kill/revive loops
        cannot launder it.

        WHY per-dispatch is safe: each episode is independently
        bounded (budget re-armed from zero AND enforced within the
        episode); no path resets the budget mid-arc — the reset
        channels are terminal / attested allow / fresh-episode
        sentinel, all of which END or legitimately restart the arc.
        """
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        # Arm 1 — revival WITH a fresh user message (recovery
        # commission): the sentinel resets the stale budget once.
        sentinel = HumanMessage(
            content="recover and finish the promote",
            id="recovery-msg-1",
            additional_kwargs={"fresh_episode_attestation_reset": True},
        )
        state = self._ceremony_state()
        state["messages"].insert(0, sentinel)
        state[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] = 8  # stale from prior arc
        state[ATTESTATION_RUN_DENY_COUNT_KEY] = 99  # stale from prior arc
        node, _manager, ledger = _make_gate_node(
            instance_id="ceremony-restart-it",
            denied_count_getter=lambda: 3,
        )

        result1 = asyncio.run(
            node(
                state,
                config={
                    "configurable": {"thread_id": "ceremony-restart-it"}
                },
            )
        )
        # The stale budget did NOT instantly loud-terminal the fresh
        # episode: the sentinel reset fired BEFORE the body read
        # (pre-body state rebuild), so the deny ran on the FRESH
        # counters and consumed slot 1 of the new episode's own
        # budget.
        assert result1["attestation_route"] == "agent"
        assert result1[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] == 1
        assert result1[ATTESTATION_RUN_DENY_COUNT_KEY] == 1

        # Arm 2 — the fresh episode's own arc now accumulates: drive
        # the never-spoke deny loop from withhold=1 to the budget.
        # Total arc length across both arms is exactly the budget
        # (1 slot consumed by Arm 1).
        state.update(
            {k: v for k, v in result1.items() if k != "messages"}
        )
        state["messages"].extend(result1.get("messages", []))
        with caplog.at_level("INFO", logger="daemon.graph"):
            denies, final = _drive_gate_loop(
                node, state, reply_text=self.CEREMONY_REPLY
            )
        assert len(denies) == WITHHOLD_DENY_BUDGET - 1
        assert final["attestation_route"] is None
        ledger.set_escalated_and_reset.assert_called_once()

        # Arm 3 — mid-loop resume WITHOUT a fresh user message: no
        # sentinel in history, budget CARRIES (no laundering).
        state2 = self._ceremony_state()  # no sentinel
        state2[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] = 5
        node2, _manager2, _ledger2 = _make_gate_node(
            instance_id="ceremony-resume-it",
            denied_count_getter=lambda: 3,
        )
        result2 = asyncio.run(
            node2(
                state2,
                config={
                    "configurable": {"thread_id": "ceremony-resume-it"}
                },
            )
        )
        assert result2["attestation_route"] == "agent"
        # 5 carried + 1 conversion = 6 (budget NOT reset).
        assert result2[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] == 6


class TestRunDenyCap:
    """b3 — the per-mission run-deny cap (belt-and-braces behind
    P1/P2/P3). Independent of the deny epoch and of the P1 withhold
    channel; trips BEFORE GRAPH_RECURSION_LIMIT and via the STANDARD
    escalation machinery, never as a GraphRecursionError."""

    def test_cap_defaults_pinned(self):
        """Pin the default (100) across the module global AND the
        config field — any change forces deliberate review of the
        sizing argument (~12x the P1 budget; below the 300-step
        recursion limit at 2 steps/cycle)."""
        import daemon.graph as graph_mod

        assert ATTESTATION_RUN_DENY_CAP == 100
        assert LimitsConfig.model_fields["attestation_run_deny_cap"].default == 100
        assert graph_mod.ATTESTATION_RUN_DENY_CAP == 100
        assert ATTESTATION_RUN_DENY_COUNT_KEY == "attestation_run_deny_count"

    def test_under_cap_denies_continue_and_increment(
        self, monkeypatch
    ):
        """Below the cap, guard-surviving denies continue as today —
        each consuming one slot of the backstop budget."""
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, _ledger = _make_gate_node(
            instance_id="cap-under-it",
            denied_count_getter=lambda: 3,
        )
        state = _delegated_without_attest_messages("Holding.")
        state[ATTESTATION_RUN_DENY_COUNT_KEY] = 5

        result = asyncio.run(
            node(
                state,
                config={"configurable": {"thread_id": "cap-under-it"}},
            )
        )
        assert result["attestation_route"] == "agent"
        assert result[ATTESTATION_RUN_DENY_COUNT_KEY] == 6

    def test_cap_exhausted_forces_loud_terminal(
        self, monkeypatch, caplog
    ):
        """At the cap the deny continuation converts to the LOUD
        terminal (standard escalation machinery + distinct audit
        row) — and b3 fired, NOT P1 (the withhold channel was
        inert)."""
        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        node, _manager, ledger = _make_gate_node(
            instance_id="cap-exhaust-it",
            denied_count_getter=lambda: 3,
        )
        state = _delegated_without_attest_messages("Holding.")
        state[ATTESTATION_RUN_DENY_COUNT_KEY] = ATTESTATION_RUN_DENY_CAP

        with caplog.at_level("INFO", logger="daemon.graph"):
            result = asyncio.run(
                node(
                    state,
                    config={
                        "configurable": {"thread_id": "cap-exhaust-it"}
                    },
                )
            )

        assert result["attestation_route"] is None
        ledger.set_escalated_and_reset.assert_called_once()
        assert "messages" not in result  # loud, not a deny
        assert result[ATTESTATION_RUN_DENY_COUNT_KEY] == 0
        log_text = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "event=leader_completion_gate_run_cap_exit" in log_text
        # P1 stayed out — the withhold channel was inert.
        assert "event=leader_completion_gate_withholding_exit" not in log_text
        # Round-2 firing-order pin: the NODE substitution token fired;
        # the router backstop token did NOT (the router must never
        # preempt the node's loud terminal at the cap).
        assert (
            "event=leader_completion_gate_run_cap_router_backstop"
            not in log_text
        )

    def test_cap_independent_of_withhold_channel(
        self, monkeypatch, caplog
    ):
        """Independence proof: with the P1 withhold channel held at 0
        (as if lost/corrupted) and the cap lowered to 3, a
        never-spoke deny loop STILL terminates — via b3, within
        budget+1 evaluations. The bound survives a P1 channel loss."""
        from daemon.graph import create_attestation_gate_node as _factory

        monkeypatch.setattr(
            judge_mod, "_invoke_judge_llm", _judge_never_called
        )
        # Patch the FACTORY'S globals (not ``sys.modules["daemon.graph"]``):
        # the real-graph fixture tests fork the daemon.graph sys.modules
        # entry, and a module-object monkeypatch would then bind to the
        # fork while the node closure still reads the original namespace
        # (the repo-documented stale-module-monkeypatch flake).
        monkeypatch.setitem(
            _factory.__globals__, "ATTESTATION_RUN_DENY_CAP", 3
        )
        node, _manager, ledger = _make_gate_node(
            instance_id="cap-indep-it",
            denied_count_getter=lambda: 3,
        )
        state = _delegated_without_attest_messages("Holding.")
        state[ATTESTATION_WITHHOLD_DENY_COUNT_KEY] = 0  # P1 inert

        # Non-degenerate, non-identical reply text: the content
        # guards (P2/P3) must stay OUT of this test — b3 is the
        # ONLY operative bound (P1 held at 0, P2 reset by the
        # interleaved polls, P3 needs ≤3-word finals).
        reply = "Status is unchanged for now. Holding steady."

        with caplog.at_level("INFO", logger="daemon.graph"):
            denies, final = _drive_gate_loop(node, state, reply_text=reply)

        assert len(denies) == 3
        assert final["attestation_route"] is None
        ledger.set_escalated_and_reset.assert_called_once()
        log_text = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "event=leader_completion_gate_run_cap_exit" in log_text
        assert "event=leader_completion_gate_withholding_exit" not in log_text
        assert "degenerate_terminal" not in log_text
        assert "consecutive_identical_terminal" not in log_text
        # Round-2 firing-order pin: node token fired, router token silent.
        assert (
            "event=leader_completion_gate_run_cap_router_backstop"
            not in log_text
        )

    def test_router_does_not_fire_at_cap(self, caplog):
        """Round-2 firing-order pin (council CRITICAL): AT the cap
        the NODE owns the exit — its substitution converts on the
        pre-write check ``count + 1 > cap``. The router must NOT
        preempt it: route 'agent' at exactly the cap routes to the
        agent (the node will loud-terminal on its next evaluation),
        and the router token stays silent."""
        state = {
            "attestation_route": "agent",
            ATTESTATION_RUN_DENY_COUNT_KEY: ATTESTATION_RUN_DENY_CAP,
        }
        with caplog.at_level("ERROR", logger="daemon.graph"):
            verdict = should_end_attestation(state)
        assert verdict == "agent"
        log_text = "\n".join(rec.getMessage() for rec in caplog.records)
        assert (
            "event=leader_completion_gate_run_cap_router_backstop"
            not in log_text
        )

    def test_router_backstop_forces_end_past_cap(self, caplog):
        """The router backstop fires ONLY when the persisted count
        EXCEEDS the cap (cap+1) — proof the node's at-cap
        substitution was skipped (a genuine double fault: node logic
        fault or counter drift past the cap). Converts the would-be
        recursion_limit death into a bounded logged END."""
        state = {
            "attestation_route": "agent",
            ATTESTATION_RUN_DENY_COUNT_KEY: ATTESTATION_RUN_DENY_CAP + 1,
        }
        with caplog.at_level("ERROR", logger="daemon.graph"):
            verdict = should_end_attestation(state)
        assert verdict is GRAPH_END
        log_text = "\n".join(rec.getMessage() for rec in caplog.records)
        assert (
            "event=leader_completion_gate_run_cap_router_backstop"
            in log_text
        )

    def test_router_under_cap_routes_normally(self):
        """Below the cap the router is byte-identical to the legacy
        behavior: hint 'agent' routes to the agent, absent hint
        ends."""
        assert (
            should_end_attestation(
                {
                    "attestation_route": "agent",
                    ATTESTATION_RUN_DENY_COUNT_KEY: 5,
                }
            )
            == "agent"
        )
        assert (
            should_end_attestation(
                {
                    "attestation_route": None,
                    ATTESTATION_RUN_DENY_COUNT_KEY: 500,
                }
            )
            is GRAPH_END
        )
        assert should_end_attestation({}) is GRAPH_END
