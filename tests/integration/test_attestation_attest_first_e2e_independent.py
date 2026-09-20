"""Independent LCA attest-first EVIDENCE variants (a)(b)(c) — merge-gate
acceptance evidence (2026-09-19, c5d9a38a remediation; D-ENTRY 2026-09-19,
``agents-ensemble/.agents/shared/planning/leader-completion-attestation/decisions.md``
lines 1864-1940).

INDEPENDENT means: this file owns its own harness — own state
construction, own fake manager/ledger stubs for ONLY what is
outside the gate+tool seam (descendant counts, ledger persistence,
R2 facade reads). The GATE NODE and the ``attest_completion`` TOOL
are the REAL production objects (no mocks on them).

Three variants (per the matrix pack the user requested):

  (a) HAPPY PATH pure-turn — clean attest_call + subsequent
      standalone report → ALLOWED with counter reset; transcript
      carries ZERO reminder messages; counter never touched.

  (b) c5d9a38a BUNDLED — non-empty content + ``attest_completion``
      tool_call in ONE AIMessage → HOLD + bundled reminder
      injection → corrected re-issue → ALLOWED. The transcript
      BOTH holds with the bundled reminder AND ends with the
      post-correction standalone report; counter still 0.

  (c) ATTEST-ONLY clean turn-end — clean attest_call + short
      text-only ack → HOLD + clean reminder injection →
      standalone report → ALLOWED. The transcript BOTH holds
      with the clean reminder AND ends with the standalone
      report; counter still 0.

The companion canonical evidence file is
``tests/integration/test_attestation_attest_first_e2e.py`` (the
two tests the user-requested matrix run produced). This file is
the SECOND independent line of evidence — distinct harness,
distinct synthetic states, distinct assertions, distinct log-row
checks. Both lines together close the c5d9a38a class end-to-end.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from daemon.graph import (
    ATTESTATION_BUNDLED_REMINDER,
    ATTESTATION_FINAL_REPORT_REMINDER,
    create_attestation_gate_node,
)
from daemon.services.attestation_gate import (
    GateSettings,
    build_gate_config,
)
from daemon.tools.attestation import (
    ATTEST_BUNDLED_RESULT_TEXT,
    ATTEST_CLEAN_RESULT_TEXT,
    attest_completion,
    reset_attest_caller_content_for_tests,
    set_attest_caller_content,
)


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────


# Stable instance id — the gate node and the synthetic states both
# thread through this. The autouse resolver-reset fixture below
# ensures the resolver caches do not leak between tests.
INSTANCE_ID = "attestation-leader-indep"

# Gate settings — window 3 (D4), deny_bound 3 (D5), mode "enforce"
# (D2 — only enforce writes the ledger; dry is a passive observer).
# The attested-allow path requires the enforce mode to fire the
# counter reset (trigger 1, ruling 1).
SETTINGS = GateSettings(mode="enforce", window=3, deny_bound=3)

# ``llm_judge_enabled=False`` in the gate config — keeps the test
# hermetic (no LLM HTTP, no judge stub needed). The gate's
# attested-allow and HOLD branches are independent of the LLM
# judge; setting the kill-switch off only short-circuits the
# fused-judge block (graph.py:5451).
_LLM_JUDGE_ENABLED = False

# Standalone report text — the FINAL AIMessage that satisfies the
# 2026-09-19 attested-allow path's standalone-report requirement
# (no tool calls, >= ``SHORT_REPORT_WORD_THRESHOLD`` = 150 words).
# Built independently from the existing evidence file's
# ``LONG_REPORT_TEXT`` to assert the harness does NOT inherit the
# dev's fixture.
LONG_REPORT_TEXT = (
    "All work is finished and the test matrix is green end-to-end "
    "across every environment we maintain. The cache TTL calculator "
    "was patched to handle both the elapsed-second and the wall-clock-"
    "second boundaries at second and minute granularity, and the unit "
    "tests exercise both arms without flake. The dead imports in the "
    "worker pool module were removed after the migration, including "
    "the legacy compatibility shim and the related test scaffolding. "
    "The error-reporting decorator was refactored so the stack-frame "
    "metadata is consistent across all four call sites in the graph "
    "node and the manager facade. The missing operator-boot log line "
    "for the new resolver module was added so operators can grep the "
    "boot summary for the resolved effective values during incident "
    "triage. The integration tests pass cleanly on the post-merge "
    "worktree; the soak run showed no flake across the full partition "
    "sweep. No follow-ups outstanding; the mission is complete and "
    "ready for review by the next teammate in the chain of delegation "
    "that will pick this up after this turn."
)

# c5d9a38a bundled-shape text — non-empty content carrying an
# ``attest_completion`` tool_call in ONE AIMessage. The runtime
# hook (``set_attest_caller_content``) reads this content via the
# per-thread ``ContextVar``; the tool body returns
# ``ATTEST_BUNDLED_RESULT_TEXT`` because the flattened content is
# non-empty. Word count is intentionally high enough that
# ``final_ai_is_text_report`` is False ONLY because of the
# ``tool_calls`` presence (the c5d9a38a class — the bundled shape
# triggers HOLD regardless of the word count because the FINAL AI
# has tool_calls).
BUNDLED_TEXT = (
    "Bundled shape: report content + attest_completion tool_call in "
    "ONE AIMessage (the c5d9a38a class). The runtime hook picks "
    "this up via the per-thread ContextVar, the tool body returns "
    "the BUNDLED teacher text, the gate classifies the final AI as "
    "a bundled shape, and decide() returns HOLD with the BUNDLED "
    "reminder text. The leader must then re-issue the standalone "
    "report on the following turn as a clean AIMessage without "
    "tool_calls and at or above SHORT_REPORT_WORD_THRESHOLD words "
    "so the gate's attested-allow path fires with counter reset."
)

# Short text-only ack — used in variant (c) to make the FINAL AI
# a short text-only AIMessage after the clean attest tool result.
# The gate sees this as the LAST AI in the bounded tail: no
# tool_calls, word count below 150 → ``final_ai_is_text_report``
# False → HOLD with the CLEAN reminder (because ``is_bundled_call``
# is False — the FINAL AI has no tool_calls so the c5d9a38a
# bundled shape does not apply).
SHORT_TEXT_ACK = "ok."


# ─────────────────────────────────────────────────────────────────────────────
# Harness — independent of the existing test's fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _make_manager_stub(
    pending_children: int = 0,
    queued_wakeups: int = 0,
    live_descendants: int = 0,
    busy_descendants: int = 0,
) -> MagicMock:
    """MagicMock manager exposing the THREE-input R2 facades (D5)
    + the FOURTH busy-descendants LCA facade (2026-09-12) + the
    user-answer-pending facade (2026-09-16, FIX-2) + the
    question-pause / watchover flags the gate's surrounding logic
    reads. The defaults are ALL ZERO — the canonical "no work
    pending" baseline so the gate evaluates the R2 deny path
    (then the attested-allow / HOLD branch decides)."""
    manager = MagicMock()
    manager.count_pending_children.return_value = pending_children
    manager.get_queued_or_expected_wakeups.return_value = queued_wakeups
    manager.count_live_descendants.return_value = live_descendants
    manager.count_busy_descendants.return_value = busy_descendants
    manager.has_open_user_answer.return_value = False
    manager.is_watchover_enabled.return_value = False
    manager.is_question_pause_requested.return_value = False
    manager.get_tree_ids_permanent.return_value = []
    manager.enqueue_message = MagicMock()
    manager.send_message = MagicMock()
    manager.revive = MagicMock()
    manager.config = None  # avoid load_config() fallback in judge path
    return manager


def _make_ledger_stub(denied_count: int = 0) -> MagicMock:
    """MagicMock ledger — Phase 3 ledger with the four CRUD
    methods (D2 — only enforce mode writes). ``denied_count``
    controls the value the stub's ``get`` returns so a test that
    wants to assert counter reset semantics threads a counter
    that started non-zero. ``set_metadata`` is also stubbed so the
    transient gate-exception marker (graph.py:5043) does not
    AttributeError if it fires."""
    ledger = MagicMock()
    ledger.increment.return_value = 1
    ledger.reset.return_value = True
    ledger.set_escalated.return_value = True
    ledger.set_escalated_and_reset.return_value = True
    ledger.get.return_value = denied_count
    ledger.set_metadata = MagicMock()
    return ledger


def _make_real_gate_node(
    instance_id: str = INSTANCE_ID,
    denied_count: int = 0,
    manager: MagicMock | None = None,
    ledger: MagicMock | None = None,
) -> tuple[Any, MagicMock, MagicMock]:
    """Build the REAL gate node via the production factory closure.

    Returns ``(gate_node, manager, ledger)``. The factory captures
    the per-instance manager handle, instance id, settings, and
    gate config at GRAPH-BUILD time; the node receives ``state``
    (and config) only at run time. Mirrors the test seam from
    ``test_attestation_stage2_failopen.py`` but with INDEPENDENT
    defaults (different instance id, own stubs)."""
    manager = manager if manager is not None else _make_manager_stub()
    ledger = ledger if ledger is not None else _make_ledger_stub(denied_count)
    config = build_gate_config(
        instance_id,
        SETTINGS,
        llm_judge_enabled=_LLM_JUDGE_ENABLED,
    )
    node = create_attestation_gate_node(
        config,
        SETTINGS,
        manager,
        instance_id,
        denied_count_getter=lambda: denied_count,
        ledger=ledger,
    )
    return node, manager, ledger


# ─────────────────────────────────────────────────────────────────────────────
# Real-tool invocation helpers (the attest_completion tool body,
# not a stub — exercised by the runtime hook + tool body together)
# ─────────────────────────────────────────────────────────────────────────────


def _attest_clean_tool_message(call_id: str) -> ToolMessage:
    """Invoke the REAL ``attest_completion`` tool body with the
    runtime hook set to a WHITESPACE-ONLY caller AIMessage (the
    clean-call shape). Returns a ``ToolMessage`` carrying
    ``ATTEST_CLEAN_RESULT_TEXT``.

    The runtime hook (``set_attest_caller_content``) writes the
    flattened caller-AIMessage content into the per-thread
    ``ContextVar``; the tool body reads that context var via
    ``_resolve_attest_caller_content()`` and picks the clean text
    when the caller content ``strip()``s to empty. We explicitly
    invoke the tool (LangChain's ``BaseTool.invoke({})`` — same
    code path ``wrapped_tools_node`` uses) so the real body
    executes, then wrap the result in a ``ToolMessage`` so the
    gate sees the same shape a ToolNode would produce.

    NOTE — why the whitespace-only sentinel? The
    ``attest_completion`` tool body has a DEFENSE-IN-DEPTH
    fallback (``_fallback_extract_attest_caller_content``) that
    walks ``inspect.stack()`` looking for an AIMessage with an
    ``attest_completion`` tool_call in any frame's locals. When
    the runtime ContextVar is set to the empty string ``""``,
    the tool body's first check ``if runtime_content: return
    runtime_content`` falls through (empty string is falsy) and
    the fallback fires. This test's outer function holds
    bundled AIMessages in its locals; the fallback would pick
    the WRONG one (an earlier bundled AIMessage carrying
    non-empty content) and return the BUNDLED teacher text. To
    exercise the runtime hook path WITHOUT the fallback, the
    ContextVar must be NON-EMPTY (so ``if runtime_content:``
    wins) BUT strip to empty (so the tool body's
    ``caller_content.strip()`` check returns False). A single
    space ``" "`` satisfies both constraints — it is a
    semantically valid "empty caller content" (an AIMessage
    carrying only whitespace is structurally distinct from one
    carrying a bundled report) and the production tool body
    treats whitespace-only callers as clean."""
    try:
        set_attest_caller_content(
            AIMessage(
                content=" ",  # whitespace-only sentinel — see NOTE above
                tool_calls=[
                    {"name": "attest_completion", "args": {}, "id": call_id}
                ],
            )
        )
        result = attest_completion.invoke({})
        assert result == ATTEST_CLEAN_RESULT_TEXT, (
            f"REAL attest_completion tool returned the wrong teacher "
            f"text for clean caller; expected "
            f"{ATTEST_CLEAN_RESULT_TEXT!r}, got {result!r}"
        )
        return ToolMessage(
            content=result,
            tool_call_id=call_id,
            name="attest_completion",
        )
    finally:
        reset_attest_caller_content_for_tests()


def _attest_bundled_tool_message(
    call_id: str, bundled_text: str
) -> ToolMessage:
    """Invoke the REAL ``attest_completion`` tool body with the
    runtime hook set to a NON-EMPTY caller AIMessage (the
    c5d9a38a bundled shape). Returns a ``ToolMessage`` carrying
    ``ATTEST_BUNDLED_RESULT_TEXT``. Symmetric to
    ``_attest_clean_tool_message``; the runtime hook sees the
    non-empty caller content and the tool body picks the bundled
    teacher text."""
    try:
        set_attest_caller_content(
            AIMessage(
                content=bundled_text,
                tool_calls=[
                    {"name": "attest_completion", "args": {}, "id": call_id}
                ],
            )
        )
        result = attest_completion.invoke({})
        assert result == ATTEST_BUNDLED_RESULT_TEXT, (
            f"REAL attest_completion tool returned the wrong teacher "
            f"text for bundled caller; expected "
            f"{ATTEST_BUNDLED_RESULT_TEXT!r}, got {result!r}"
        )
        return ToolMessage(
            content=result,
            tool_call_id=call_id,
            name="attest_completion",
        )
    finally:
        reset_attest_caller_content_for_tests()


# ─────────────────────────────────────────────────────────────────────────────
# AIMessage factories (the synthetic transcript shapes)
# ─────────────────────────────────────────────────────────────────────────────


def _delegated_ai(call_id: str = "indep-dispatch") -> AIMessage:
    """The first AIMessage — the delegation that anchors the
    conditional gate ON (the delegation scan answers "did this
    mission dispatch children"). The conditional-attestation
    flag (``attestation_required``) is True only when a
    ``send_message`` tool call appears in the tail after the
    last real user message — without it, the gate is OFF and
    the matrix would never exercise the attested-allow /
    HOLD branches."""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "send_message",
                "args": {"target": "child-id"},
                "id": call_id,
            }
        ],
    )


def _send_message_tool_message(call_id: str = "indep-dispatch") -> ToolMessage:
    """The send_message tool result — a real ToolMessage shape
    the leader reads after the delegation. Content is a stub
    reply; only the tool_call_id link matters for the gate's
    delegation scan."""
    return ToolMessage(
        content="Delegation acknowledged; child will report.",
        tool_call_id=call_id,
        name="send_message",
    )


def _clean_attest_ai(call_id: str = "indep-attest-clean") -> AIMessage:
    """The CLEAN attest_call — empty content +
    ``attest_completion`` tool call. The 2026-09-19 contract's
    required shape for the FIRST half of the deliver-then-attest
    sequence."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "attest_completion", "args": {}, "id": call_id}
        ],
    )


def _bundled_attest_ai(
    content: str, call_id: str = "indep-attest-bundled"
) -> AIMessage:
    """The BUNDLED attest_call — non-empty content +
    ``attest_completion`` tool call in ONE AIMessage (the
    c5d9a38a shape)."""
    return AIMessage(
        content=content,
        tool_calls=[
            {"name": "attest_completion", "args": {}, "id": call_id}
        ],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — resolver cache reset + log capture
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_resolvers_between_tests(monkeypatch):
    """Clear kill-switch envs + reset the canonical resolver
    cache + the LLM-judge resolvers so each test sees a clean
    resolver state. Mirrors the test seam from
    ``test_attestation_stage2_failopen.py``. Hermetic isolation
    per blueprint (c)."""
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
    )
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_MODE", raising=False
    )
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S", raising=False
    )
    from daemon.services.attestation_resolver import (
        reset_attestation_resolver_for_tests,
    )
    from daemon.services.attestation_judge_resolver import (
        reset_llm_judge_resolver_for_tests,
    )
    from daemon.services.attestation_judge_timeout_resolver import (
        reset_judge_timeout_resolver_for_tests,
    )

    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    reset_judge_timeout_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    reset_judge_timeout_resolver_for_tests()


@pytest.fixture
def gate_caplog(caplog):
    """Pin caplog at INFO on the loggers that emit gate / fused
    rows (mirrors the Stage-2 sibling test's caplog pattern).
    Returns the caplog fixture so each test can pull ``.records``
    directly."""
    for logger_name in (
        "daemon.graph",
        "daemon.services.attestation_gate",
        "daemon.services.attestation_resolver_activation",
    ):
        caplog.set_level(logging.INFO, logger=logger_name)
    return caplog


def _log_text(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


def _max_denied_count(caplog) -> int:
    """Crude parse of the canonical log token ``denied_count=N``
    — used to assert the counter never exceeded the bound
    throughout the run (property (e) of the existing evidence
    file, mirrored here for the INDEPENDENT line)."""
    max_seen = 0
    for line in _log_text(caplog).splitlines():
        for token in line.split():
            if token.startswith("denied_count="):
                try:
                    v = int(token.split("=", 1)[1])
                    max_seen = max(max_seen, v)
                except (ValueError, IndexError):
                    pass
    return max_seen


# ─────────────────────────────────────────────────────────────────────────────
# Test (a) — HAPPY PATH pure-toolcall-turn ALLOWED
# ─────────────────────────────────────────────────────────────────────────────


class TestE2EAttestFirstIndependentHappyPath:
    """Independent EVIDENCE variant (a): a scripted leader that
    emits a clean ``attest_completion`` tool call (empty content)
    followed by a standalone text report (no tool calls, ≥150
    words) is the attested-allow path → ALLOWED with counter
    reset; no reminder message anywhere; counter never
    incremented. The REAL gate node (production factory closure)
    evaluates the synthetic state; the REAL ``attest_completion``
    tool body runs to produce the canonical
    ``ATTEST_CLEAN_RESULT_TEXT`` ToolMessage."""

    def test_a_happy_path_pure_toolcall_turn_evidence(self, gate_caplog):
        """End-to-end transcript shape for the HAPPY PATH.

        Asserts:
          (a) The attest AIMessage has EMPTY content + exactly one
              ``attest_completion`` tool call (the 2026-09-19
              contract's required shape — the runtime hook
              exercises the per-thread ContextVar path with a
              whitespace-only sentinel that strips to empty; the
              fallback ``_fallback_extract_attest_caller_content``
              is NOT triggered, exercising the production runtime
              path).
          (b) The ToolMessage from the REAL ``attest_completion``
              tool carries ``ATTEST_CLEAN_RESULT_TEXT`` (the
              clean-call teacher).
          (c) The transcript's LAST AI message is the standalone
              text report (no tool calls, ≥150 words).
          (d) The gate's decision log row fires
              ``decision=allowed`` with
              ``attestation_present=True`` (the attested-allow
              path reset trigger 1).
          (e) The counter UNCHANGED throughout — the attested-
              allow reset only fires the ledger's ``reset()``;
              ``increment()`` is NEVER called on the ALLOWED
              path. Max ``denied_count`` seen across the run is
              0 (the starting counter).
          (f) NO reminder message anywhere — the HAPPY PATH does
              not exercise the HOLD branch; the transcript
              carries zero "Final Report Reminder" messages.
        """
        node, manager, ledger = _make_real_gate_node(denied_count=0)

        # Build the synthetic transcript ending in the standalone
        # report — the FINAL AI in the bounded window. The gate's
        # ``classify_final_ai_shape`` returns
        # ``final_ai_is_text_report=True`` and decide() returns
        # ``Decision.ALLOWED`` with counter reset (trigger 1).
        clean_attest_call_id = "indep-attest-clean-a"
        attest_msg = _clean_attest_ai(call_id=clean_attest_call_id)
        attest_tool_msg = _attest_clean_tool_message(clean_attest_call_id)
        report_msg = AIMessage(content=LONG_REPORT_TEXT)

        state: dict[str, Any] = {
            "messages": [
                HumanMessage(content="please do it"),
                _delegated_ai(),
                _send_message_tool_message(),
                AIMessage(
                    content="Hallucinated completion."
                ),  # turn 1 END → would deny (R2 un-attested)
                attest_msg,  # turn 2 clean attest_call
                attest_tool_msg,  # tool result (REAL tool body)
                report_msg,  # turn 2 END → ALLOWED
            ]
        }

        # Invoke the REAL gate node with the synthetic state.
        result = asyncio.run(
            node(state, config={"configurable": {"thread_id": INSTANCE_ID}})
        )
        assert result is not None, "gate node returned None"

        # ── (d) Decision: ALLOWED — no route-back, no nudge, no
        # HOLD reminder. ``attestation_route`` is absent (or "end")
        # so the wrapped ``should_end_attestation`` router routes
        # to END. ──────────────────────────────────────────────
        route = result.get("attestation_route")
        assert route is None or route == "end", (
            f"expected ALLOWED path (no route-back to agent); "
            f"got attestation_route={route!r}"
        )
        # The returned messages list MUST NOT carry a reminder —
        # the ALLOWED path does not inject one.
        returned_messages = result.get("messages", [])
        reminder_msgs_in_return = [
            m
            for m in returned_messages
            if isinstance(m, HumanMessage)
            and ("Final Report Reminder" in (m.content or ""))
        ]
        assert reminder_msgs_in_return == [], (
            f"ALLOWED path MUST NOT inject a reminder message; "
            f"got: {reminder_msgs_in_return!r}"
        )

        # ── (e) Ledger: counter reset fires on attested-allow;
        # increment NEVER fires on this path. ────────────────
        ledger.reset.assert_called_once_with(INSTANCE_ID)
        ledger.increment.assert_not_called()

        # ── Log row checks ─────────────────────────────────────
        log_text = _log_text(gate_caplog)
        allowed_rows = [
            line for line in log_text.splitlines() if "decision=allowed" in line
        ]
        assert allowed_rows, (
            "expected a ``decision=allowed`` log row at the FINAL "
            "turn-end (the standalone-report path fired)"
        )
        assert any(
            "attestation_present=True" in row for row in allowed_rows
        ), (
            f"expected the allowed row to carry "
            f"attestation_present=True; got: {allowed_rows!r}"
        )
        # Counter independence: max denied_count seen across all
        # log rows. The starting counter is 0; the attested-allow
        # reset keeps it at 0; no other branch fires in this test.
        max_seen = _max_denied_count(gate_caplog)
        assert max_seen == 0, (
            f"counter incremented from 0 — property (e) violated; "
            f"max denied_count seen: {max_seen}"
        )

        # ── (a) The attest AIMessage shape ─────────────────────
        assert not (attest_msg.content or "").strip(), (
            f"attest_call AIMessage must have empty content "
            f"(attest-first contract); got: {attest_msg.content!r}"
        )
        attest_tool_calls = attest_msg.tool_calls or []
        assert len(attest_tool_calls) == 1, (
            f"attest_call AIMessage must carry exactly one tool "
            f"call (attest_completion); got {len(attest_tool_calls)}"
        )
        assert attest_tool_calls[0]["name"] == "attest_completion"

        # ── (b) ToolMessage carries ATTEST_CLEAN_RESULT_TEXT ────
        assert ATTEST_CLEAN_RESULT_TEXT in str(
            attest_tool_msg.content or ""
        ), (
            f"REAL attest_completion tool result must carry "
            f"ATTEST_CLEAN_RESULT_TEXT; got: "
            f"{attest_tool_msg.content!r}"
        )

        # ── (c) Final AI = standalone report ───────────────────
        last_ai = next(
            (
                m
                for m in reversed(state["messages"])
                if isinstance(m, AIMessage)
            ),
            None,
        )
        assert last_ai is not None, "no AIMessage in the transcript"
        assert last_ai.content == LONG_REPORT_TEXT, (
            f"transcript's LAST AI message must be the standalone "
            f"text report; got content of length "
            f"{len((last_ai.content or '').split())} words"
        )
        assert not (last_ai.tool_calls or []), (
            f"final standalone report MUST carry zero tool calls; "
            f"got {last_ai.tool_calls!r}"
        )
        assert len((last_ai.content or "").split()) >= 150, (
            f"final standalone report MUST be >= "
            f"SHORT_REPORT_WORD_THRESHOLD (150) words; got "
            f"{len((last_ai.content or '').split())} words"
        )

        # ── (f) NO reminder message anywhere in the state ──────
        reminder_msgs_in_state = [
            m
            for m in state["messages"]
            if isinstance(m, HumanMessage)
            and ("Final Report Reminder" in (m.content or ""))
        ]
        assert reminder_msgs_in_state == [], (
            f"HAPPY PATH must not have any reminder messages in "
            f"the state; got: {reminder_msgs_in_state!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test (b) — c5d9a38a BUNDLED HOLD + bundled reminder + correction ALLOWED
# ─────────────────────────────────────────────────────────────────────────────


class TestE2EAttestFirstIndependentBundledHold:
    """Independent EVIDENCE variant (b): the c5d9a38a class —
    leader bundles report + ``attest_completion`` in ONE AIMessage
    (non-empty content + tool_call together). The transcript ends
    with the bundled AIMessage as the FINAL AI (no further
    standalone report follows), so the gate classifies the FINAL
    AI as a bundled shape and fires HOLD + the BUNDLED reminder
    injection (counter-INDEPENDENT — does NOT increment
    ``attestation_denied_count``). The leader then re-issues a
    clean attest_call + standalone report on the next turn → the
    gate fires the attested-allow path with counter reset.

    Two sequential gate-node evaluations exercise both the HOLD
    + bundled reminder path and the post-correction ALLOWED path
    against the REAL production gate node + REAL attest_completion
    tool body. The transcript's FINAL AI is the standalone report
    (the post-correction state), and the bundled reminder
    HumanMessage appears in the HOLD-returned messages."""

    def test_b_bundled_call_corrected_evidence(self, gate_caplog):
        """Two-stage EVIDENCE: (b.1) HOLD + bundled reminder
        injection; (b.2) post-correction ALLOWED.

        Stage (b.1) asserts:
          * The FINAL AI in the synthetic state is the bundled
            AIMessage (non-empty content + attest_completion
            tool_call) — the c5d9a38a class.
          * The REAL ``attest_completion`` tool body (run in the
            test setup) returns ``ATTEST_BUNDLED_RESULT_TEXT``
            for both bundled calls.
          * The gate fires HOLD + the BUNDLED reminder injection
            — the returned messages list carries a HumanMessage
            whose content matches ``ATTESTATION_BUNDLED_REMINDER``
            (the bundled re-issue teacher text).
          * The returned ``attestation_route`` is "agent" (route
            back to leader so the re-issue can happen).
          * The per-mission reminder count
            (``attestation_reminder_count``) was incremented to 1.
          * Counter UNCHANGED — HOLD does NOT touch the ledger's
            ``increment``; ``ledger.reset`` does NOT fire either.
          * The canonical decision log row fires
            ``decision=hold`` with ``is_bundled_call=True`` (the
            bundled-shape reminder selection).

        Stage (b.2) asserts:
          * The leader's correction (clean attest_call +
            standalone report) — assembled in a fresh state that
            mirrors the production post-HOLD transcript — fires
            ALLOWED with counter reset.
          * The transcript's FINAL AI is the standalone report;
            the bundled reminder HumanMessage sits earlier in the
            state (the prior HOLD's injection).
          * Counter was reset to 0 by the attested-allow path
            (the ledger's ``reset`` fires).
        """
        node_b1, manager_b1, ledger_b1 = _make_real_gate_node(denied_count=0)
        node_b2, manager_b2, ledger_b2 = _make_real_gate_node(denied_count=0)

        # ── Stage (b.1): HOLD + bundled reminder injection ────
        # The transcript ends with a SECOND bundled AIMessage
        # (the FINAL AI has tool_calls + non-empty content — the
        # c5d9a38a shape). The REAL attest_completion tool runs
        # twice (once per bundled call) and returns the BUNDLED
        # teacher text both times — the runtime hook picks it
        # from the non-empty caller content.
        bundled_call_id_1 = "indep-attest-bundled-1"
        bundled_call_id_2 = "indep-attest-bundled-2"
        bundled_msg_1 = _bundled_attest_ai(
            content=BUNDLED_TEXT, call_id=bundled_call_id_1
        )
        bundled_tool_msg_1 = _attest_bundled_tool_message(
            bundled_call_id_1, BUNDLED_TEXT
        )
        bundled_msg_2 = _bundled_attest_ai(
            content=BUNDLED_TEXT + " (second call)",
            call_id=bundled_call_id_2,
        )
        bundled_tool_msg_2 = _attest_bundled_tool_message(
            bundled_call_id_2, BUNDLED_TEXT + " (second call)"
        )

        state_b1: dict[str, Any] = {
            "messages": [
                HumanMessage(content="please do it"),
                _delegated_ai(),
                _send_message_tool_message(),
                AIMessage(content="Hallucinated completion."),
                bundled_msg_1,
                bundled_tool_msg_1,
                bundled_msg_2,  # FINAL AI = bundled (c5d9a38a)
                bundled_tool_msg_2,
            ]
        }

        result_b1 = asyncio.run(
            node_b1(
                state_b1,
                config={"configurable": {"thread_id": INSTANCE_ID}},
            )
        )
        assert result_b1 is not None, "gate node returned None on HOLD"

        # ── (b.1) Decision: HOLD + bundled reminder injection ─
        # The route hint MUST be "agent" — the gate routes back
        # so the leader can re-issue. The per-mission reminder
        # count MUST increment (capped at ATTESTATION_REMINDER_CAP).
        assert result_b1.get("attestation_route") == "agent", (
            f"HOLD MUST route back to agent for re-issue; got "
            f"attestation_route={result_b1.get('attestation_route')!r}"
        )
        # The per-mission reminder count key is
        # ``attestation_reminder_count`` (daemon/graph.py
        # ATTESTATION_REMINDER_COUNT_KEY).
        assert result_b1.get("attestation_reminder_count") == 1, (
            f"per-mission reminder count MUST increment to 1 on "
            f"HOLD; got: "
            f"{result_b1.get('attestation_reminder_count')!r}"
        )
        # The returned messages list MUST carry the BUNDLED
        # reminder HumanMessage — its content carries
        # ``ATTESTATION_BUNDLED_REMINDER`` (the canonical
        # bundled re-issue teacher text).
        returned_messages_b1 = result_b1.get("messages", [])
        assert len(returned_messages_b1) == 1, (
            f"HOLD MUST inject exactly one reminder message; got "
            f"{len(returned_messages_b1)}"
        )
        reminder_b1 = returned_messages_b1[0]
        assert isinstance(reminder_b1, HumanMessage), (
            f"HOLD reminder MUST be a HumanMessage; got "
            f"{type(reminder_b1).__name__}"
        )
        reminder_content_b1 = str(reminder_b1.content or "")
        # The reminder is wrapped in the [SYSTEM CONTEXT: Final
        # Report Reminder] header by the
        # ``_make_context_message`` factory — the body is the
        # remainder past the header. Assert the BUNDLED body text
        # (the bundled-shape unique substring) is present.
        assert (
            "tool-call message contained text" in reminder_content_b1
        ), (
            f"HOLD reminder MUST carry the BUNDLED re-issue "
            f"teacher text; got: {reminder_content_b1!r}"
        )
        # The BUNDLED reminder is the distinct substring that
        # DIFFERENTIATES it from the CLEAN reminder — assert the
        # canonical full bundled body is present.
        expected_bundled_body = (
            "Attestation received, but your tool-call message "
            "contained text - the attestation call must be "
            "text-free. Re-issue your full detailed final report "
            "now as its own standalone message."
        )
        assert expected_bundled_body in reminder_content_b1, (
            f"HOLD reminder MUST carry the FULL bundled body; "
            f"got: {reminder_content_b1!r}"
        )

        # ── (b.1) Ledger: counter UNCHANGED on HOLD ──────────
        # HOLD is counter-INDEPENDENT — it does NOT touch the
        # ledger's increment or reset. The bound/escalation
        # machinery is NEVER consulted on HOLD.
        ledger_b1.increment.assert_not_called()
        ledger_b1.reset.assert_not_called()
        ledger_b1.set_escalated_and_reset.assert_not_called()

        # ── (b.1) Log row: decision=hold with is_bundled_call=True
        log_text_b1 = _log_text(gate_caplog)
        hold_rows = [
            line for line in log_text_b1.splitlines() if "decision=hold" in line
        ]
        assert hold_rows, (
            "expected a ``decision=hold`` log row at the HOLD "
            "turn-end (the bundled-shape path fired)"
        )
        # The graph-node HOLD log row carries
        # ``is_bundled_call=True`` (graph.py:5985-5993).
        hold_log_rows = [
            r.getMessage()
            for r in gate_caplog.records
            if "hold instance=" in r.getMessage()
        ]
        assert any(
            "is_bundled_call=True" in row for row in hold_log_rows
        ), (
            f"expected the HOLD log row to carry "
            f"is_bundled_call=True (the bundled-shape path); "
            f"got: {hold_log_rows!r}"
        )
        # Counter independence: max denied_count seen across
        # the HOLD evaluation. The starting counter is 0; the
        # HOLD path does NOT increment (counter-INDEPENDENT
        # contract).
        max_seen_b1 = _max_denied_count(gate_caplog)
        assert max_seen_b1 == 0, (
            f"counter incremented from 0 on HOLD — counter-"
            f"INDEPENDENCE violated; max denied_count seen: "
            f"{max_seen_b1}"
        )

        # ── Stage (b.2): post-correction ALLOWED ─────────────
        # The leader got the BUNDLED reminder, re-issued with a
        # clean attest_call (empty + tool_call) + a standalone
        # text report. Construct the state that mirrors the
        # production post-HOLD transcript: prior bundled + tools
        # results + the bundled reminder HumanMessage (from
        # HOLD's return) + clean attest + tool result + report.
        clean_attest_call_id_b = "indep-attest-clean-b"
        attest_msg_b = _clean_attest_ai(call_id=clean_attest_call_id_b)
        attest_tool_msg_b = _attest_clean_tool_message(
            clean_attest_call_id_b
        )
        report_msg_b = AIMessage(content=LONG_REPORT_TEXT)

        state_b2: dict[str, Any] = {
            "messages": [
                HumanMessage(content="please do it"),
                _delegated_ai(),
                _send_message_tool_message(),
                AIMessage(content="Hallucinated completion."),
                bundled_msg_1,
                bundled_tool_msg_1,
                bundled_msg_2,
                bundled_tool_msg_2,
                reminder_b1,  # the HOLD-injected bundled reminder
                attest_msg_b,  # the leader's correction
                attest_tool_msg_b,  # REAL attest_completion tool result
                report_msg_b,  # FINAL AI = standalone report
            ]
        }

        result_b2 = asyncio.run(
            node_b2(
                state_b2,
                config={"configurable": {"thread_id": INSTANCE_ID}},
            )
        )
        assert result_b2 is not None, "gate node returned None on ALLOWED"

        # ── (b.2) Decision: ALLOWED with counter reset ───────
        route_b2 = result_b2.get("attestation_route")
        assert route_b2 is None or route_b2 == "end", (
            f"post-correction expected ALLOWED path (no route-"
            f"back); got attestation_route={route_b2!r}"
        )
        # No reminder injected on the ALLOWED path.
        returned_messages_b2 = result_b2.get("messages", [])
        reminder_msgs_in_b2 = [
            m
            for m in returned_messages_b2
            if isinstance(m, HumanMessage)
            and ("Final Report Reminder" in (m.content or ""))
        ]
        assert reminder_msgs_in_b2 == [], (
            f"ALLOWED path MUST NOT inject a reminder; got: "
            f"{reminder_msgs_in_b2!r}"
        )
        # Ledger reset fires on attested-allow; increment NEVER
        # fires (this is a single evaluation against the
        # ledger_b2 stub — the prior HOLD's stub was ledger_b1).
        ledger_b2.reset.assert_called_once_with(INSTANCE_ID)
        ledger_b2.increment.assert_not_called()

        # ── (b.2) Transcript shape: report is LAST AI; bundled
        # reminder sits earlier in the state. ────────────────
        last_ai_b = next(
            (
                m
                for m in reversed(state_b2["messages"])
                if isinstance(m, AIMessage)
            ),
            None,
        )
        assert last_ai_b is not None, "no AIMessage in the post-correction state"
        assert last_ai_b.content == LONG_REPORT_TEXT, (
            f"post-correction transcript's LAST AI message MUST "
            f"be the standalone text report; got content of "
            f"length {len((last_ai_b.content or '').split())} "
            f"words"
        )
        assert not (last_ai_b.tool_calls or []), (
            f"final standalone report MUST carry zero tool "
            f"calls; got {last_ai_b.tool_calls!r}"
        )
        assert len((last_ai_b.content or "").split()) >= 150
        # The bundled reminder HumanMessage sits EARLIER in the
        # state (the prior HOLD's injection, replayed by the
        # production graph's stable-id supersede contract).
        bundled_reminder_in_state = [
            m
            for m in state_b2["messages"]
            if isinstance(m, HumanMessage)
            and (
                "tool-call message contained text" in (m.content or "")
            )
        ]
        assert bundled_reminder_in_state, (
            "the bundled reminder HumanMessage from the prior "
            "HOLD MUST be present in the post-correction state"
        )

        # ── Log row: a ``decision=allowed`` row with
        # ``attestation_present=True`` (the post-correction
        # attested-allow path).
        log_text_b2 = _log_text(gate_caplog)
        allowed_rows_b2 = [
            line
            for line in log_text_b2.splitlines()
            if "decision=allowed" in line
        ]
        assert allowed_rows_b2, (
            "expected a ``decision=allowed`` log row at the "
            "post-correction turn-end"
        )
        assert any(
            "attestation_present=True" in row for row in allowed_rows_b2
        ), (
            f"expected the post-correction allowed row to carry "
            f"attestation_present=True; got: {allowed_rows_b2!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test (c) — ATTEST-ONLY clean HOLD + clean reminder + report ALLOWED
# ─────────────────────────────────────────────────────────────────────────────


class TestE2EAttestFirstIndependentAttestOnlyCleanHold:
    """Independent EVIDENCE variant (c): the leader emits a clean
    ``attest_completion`` tool call (empty content) and the FINAL
    AI in that turn is a short text-only AIMessage (no tool
    calls). The gate classifies the FINAL AI as NOT a standalone
    text report (``final_ai_is_text_report=False`` — short, no
    tool calls) and fires HOLD with the CLEAN reminder
    (``ATTESTATION_FINAL_REPORT_REMINDER`` — the clean re-issue
    teacher text). The leader then delivers the standalone
    report on the next turn → the gate fires ALLOWED with
    counter reset.

    Two sequential gate-node evaluations exercise both the HOLD
    + clean reminder path and the post-correction ALLOWED path
    against the REAL production gate node + REAL
    ``attest_completion`` tool body."""

    def test_c_attest_only_clean_hold_corrected_evidence(self, gate_caplog):
        """Two-stage EVIDENCE: (c.1) HOLD + clean reminder
        injection; (c.2) post-correction ALLOWED.

        Stage (c.1) asserts:
          * The FINAL AI in the synthetic state is a SHORT
            text-only AIMessage (no tool calls, < 150 words) —
            the attest-only shape: the leader called
            ``attest_completion`` then ended the turn with a
            short ack instead of the standalone report.
          * The REAL ``attest_completion`` tool body returns
            ``ATTEST_CLEAN_RESULT_TEXT`` (the clean teacher text
            — the runtime hook saw empty caller content).
          * The gate fires HOLD + the CLEAN reminder injection
            — the returned messages list carries a HumanMessage
            whose content matches
            ``ATTESTATION_FINAL_REPORT_REMINDER`` (the clean
            re-issue teacher text; distinct from the BUNDLED
            reminder).
          * The returned ``attestation_route`` is "agent" (route
            back to leader).
          * The per-mission reminder count
            (``attestation_reminder_count``) was incremented to 1.
          * Counter UNCHANGED — HOLD does NOT touch the ledger's
            ``increment``; ``ledger.reset`` does NOT fire either.
          * The canonical decision log row fires
            ``decision=hold`` with ``is_bundled_call=False``
            (the clean-call HOLD — distinct from the bundled
            variant (b)).

        Stage (c.2) asserts:
          * The leader's correction (standalone report) fires
            ALLOWED with counter reset.
          * The transcript's FINAL AI is the standalone report;
            the clean reminder HumanMessage sits earlier in the
            state (the prior HOLD's injection).
          * Counter was reset to 0 by the attested-allow path
            (the ledger's ``reset`` fires).
        """
        node_c1, manager_c1, ledger_c1 = _make_real_gate_node(denied_count=0)
        node_c2, manager_c2, ledger_c2 = _make_real_gate_node(denied_count=0)

        # ── Stage (c.1): HOLD + clean reminder injection ──────
        # The transcript ends with a SHORT text-only AIMessage
        # (no tool_calls, 1 word) — the FINAL AI in the bounded
        # tail. The REAL attest_completion tool runs once (the
        # clean attest call) and returns the CLEAN teacher text.
        clean_attest_call_id_c = "indep-attest-clean-c"
        attest_msg_c = _clean_attest_ai(call_id=clean_attest_call_id_c)
        attest_tool_msg_c = _attest_clean_tool_message(
            clean_attest_call_id_c
        )
        short_ack_msg = AIMessage(content=SHORT_TEXT_ACK)

        state_c1: dict[str, Any] = {
            "messages": [
                HumanMessage(content="please do it"),
                _delegated_ai(),
                _send_message_tool_message(),
                AIMessage(content="Hallucinated completion."),
                attest_msg_c,  # clean attest_call (turn 2)
                attest_tool_msg_c,  # REAL clean tool result
                short_ack_msg,  # FINAL AI = short text-only ack
            ]
        }

        result_c1 = asyncio.run(
            node_c1(
                state_c1,
                config={"configurable": {"thread_id": INSTANCE_ID}},
            )
        )
        assert result_c1 is not None, "gate node returned None on HOLD"

        # ── (c.1) Decision: HOLD + clean reminder injection ─
        # Route hint MUST be "agent" — the gate routes back so
        # the leader can deliver the report.
        assert result_c1.get("attestation_route") == "agent", (
            f"HOLD MUST route back to agent for re-issue; got "
            f"attestation_route={result_c1.get('attestation_route')!r}"
        )
        assert result_c1.get("attestation_reminder_count") == 1, (
            f"per-mission reminder count MUST increment to 1 on "
            f"HOLD; got: "
            f"{result_c1.get('attestation_reminder_count')!r}"
        )
        # The returned messages list MUST carry the CLEAN
        # reminder HumanMessage — its content carries
        # ``ATTESTATION_FINAL_REPORT_REMINDER`` (the canonical
        # clean re-issue teacher text), NOT the bundled one.
        returned_messages_c1 = result_c1.get("messages", [])
        assert len(returned_messages_c1) == 1, (
            f"HOLD MUST inject exactly one reminder message; got "
            f"{len(returned_messages_c1)}"
        )
        reminder_c1 = returned_messages_c1[0]
        assert isinstance(reminder_c1, HumanMessage), (
            f"HOLD reminder MUST be a HumanMessage; got "
            f"{type(reminder_c1).__name__}"
        )
        reminder_content_c1 = str(reminder_c1.content or "")
        # The CLEAN reminder is the distinct substring that
        # DIFFERENTIATES it from the BUNDLED reminder.
        expected_clean_body = (
            "Attestation received. Deliver your full detailed "
            "final report now as your final message."
        )
        assert expected_clean_body in reminder_content_c1, (
            f"HOLD reminder MUST carry the FULL clean body; "
            f"got: {reminder_content_c1!r}"
        )
        # The CLEAN reminder MUST NOT carry the BUNDLED-specific
        # substring (the c5d9a38a shape is not in play here).
        assert (
            "tool-call message contained text"
            not in reminder_content_c1
        ), (
            f"CLEAN HOLD MUST NOT carry the bundled-substring; "
            f"got: {reminder_content_c1!r}"
        )

        # ── (c.1) Ledger: counter UNCHANGED on HOLD ──────────
        ledger_c1.increment.assert_not_called()
        ledger_c1.reset.assert_not_called()
        ledger_c1.set_escalated_and_reset.assert_not_called()

        # ── (c.1) Log row: decision=hold with is_bundled_call=False
        hold_log_rows_c1 = [
            r.getMessage()
            for r in gate_caplog.records
            if "hold instance=" in r.getMessage()
        ]
        assert hold_log_rows_c1, (
            "expected a ``hold instance=`` log row at the HOLD "
            "turn-end (the clean-shape HOLD path fired)"
        )
        assert any(
            "is_bundled_call=False" in row for row in hold_log_rows_c1
        ), (
            f"expected the CLEAN HOLD log row to carry "
            f"is_bundled_call=False (the clean-shape path — "
            f"distinct from the bundled variant (b)); got: "
            f"{hold_log_rows_c1!r}"
        )
        # Counter independence.
        max_seen_c1 = _max_denied_count(gate_caplog)
        assert max_seen_c1 == 0, (
            f"counter incremented from 0 on HOLD — counter-"
            f"INDEPENDENCE violated; max denied_count seen: "
            f"{max_seen_c1}"
        )

        # ── Stage (c.2): post-correction ALLOWED ─────────────
        # The leader got the CLEAN reminder, delivered the
        # standalone text report as a subsequent standalone
        # AIMessage (no tool_calls). Construct the state that
        # mirrors the production post-HOLD transcript.
        report_msg_c = AIMessage(content=LONG_REPORT_TEXT)

        state_c2: dict[str, Any] = {
            "messages": [
                HumanMessage(content="please do it"),
                _delegated_ai(),
                _send_message_tool_message(),
                AIMessage(content="Hallucinated completion."),
                attest_msg_c,
                attest_tool_msg_c,
                short_ack_msg,
                reminder_c1,  # the HOLD-injected CLEAN reminder
                report_msg_c,  # FINAL AI = standalone report
            ]
        }

        result_c2 = asyncio.run(
            node_c2(
                state_c2,
                config={"configurable": {"thread_id": INSTANCE_ID}},
            )
        )
        assert result_c2 is not None, "gate node returned None on ALLOWED"

        # ── (c.2) Decision: ALLOWED with counter reset ───────
        route_c2 = result_c2.get("attestation_route")
        assert route_c2 is None or route_c2 == "end", (
            f"post-correction expected ALLOWED path (no route-"
            f"back); got attestation_route={route_c2!r}"
        )
        # No reminder injected on the ALLOWED path.
        returned_messages_c2 = result_c2.get("messages", [])
        reminder_msgs_in_c2 = [
            m
            for m in returned_messages_c2
            if isinstance(m, HumanMessage)
            and ("Final Report Reminder" in (m.content or ""))
        ]
        assert reminder_msgs_in_c2 == [], (
            f"ALLOWED path MUST NOT inject a reminder; got: "
            f"{reminder_msgs_in_c2!r}"
        )
        # Ledger reset fires on attested-allow.
        ledger_c2.reset.assert_called_once_with(INSTANCE_ID)
        ledger_c2.increment.assert_not_called()

        # ── (c.2) Transcript shape: report is LAST AI; clean
        # reminder sits earlier in the state. ────────────────
        last_ai_c = next(
            (
                m
                for m in reversed(state_c2["messages"])
                if isinstance(m, AIMessage)
            ),
            None,
        )
        assert last_ai_c is not None, "no AIMessage in the post-correction state"
        assert last_ai_c.content == LONG_REPORT_TEXT, (
            f"post-correction transcript's LAST AI message MUST "
            f"be the standalone text report; got content of "
            f"length {len((last_ai_c.content or '').split())} "
            f"words"
        )
        assert not (last_ai_c.tool_calls or []), (
            f"final standalone report MUST carry zero tool "
            f"calls; got {last_ai_c.tool_calls!r}"
        )
        assert len((last_ai_c.content or "").split()) >= 150
        # The CLEAN reminder HumanMessage sits EARLIER in the
        # state (the prior HOLD's injection, replayed by the
        # production graph's stable-id supersede contract).
        clean_reminder_in_state = [
            m
            for m in state_c2["messages"]
            if isinstance(m, HumanMessage)
            and (
                "Deliver your full detailed final report now as "
                "your final message." in (m.content or "")
            )
        ]
        assert clean_reminder_in_state, (
            "the CLEAN reminder HumanMessage from the prior "
            "HOLD MUST be present in the post-correction state"
        )

        # ── Log row: a ``decision=allowed`` row with
        # ``attestation_present=True``.
        log_text_c2 = _log_text(gate_caplog)
        allowed_rows_c2 = [
            line
            for line in log_text_c2.splitlines()
            if "decision=allowed" in line
        ]
        assert allowed_rows_c2, (
            "expected a ``decision=allowed`` log row at the "
            "post-correction turn-end"
        )
        assert any(
            "attestation_present=True" in row for row in allowed_rows_c2
        ), (
            f"expected the post-correction allowed row to carry "
            f"attestation_present=True; got: {allowed_rows_c2!r}"
        )
