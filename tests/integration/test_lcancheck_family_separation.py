"""LCA Completion Check Note family SEPARATION — merge gate (Job 3,
``feature/lca-remove-check-note`` @ ``ff9eb8492a05aad485a17d94da12fe865bde0342``,
base ``6bf7bed7``).

Live verification that the LCA Completion Check Note family removal is
FAMILY-ISOLATED from the attest-first Final Report Reminder family. The
removal retired ONLY the Completion Check Note family; the attest-first
Final Report Reminder family MUST be UNTOUCHED. The two families share
the ``attestation_route="agent"`` + ``HumanMessage`` injection shape but
NEVER the title, body, stable-id kind, or counter channel.

Three gates:

  S1 — HOLD → Final Report Reminder STILL injects (live)
        Drive the REAL gate node via the production
        ``create_attestation_gate_node`` factory closure. Two
        sub-scenarios: (S1a) clean attest_call + short ack → HOLD with
        the CLEAN Final Report Reminder; (S1b) bundled attest_call
        (c5d9a38a shape) → HOLD with the BUNDLED Final Report
        Reminder. Both inject exactly one ``HumanMessage`` carrying the
        ``[SYSTEM CONTEXT: Final Report Reminder]`` title, the canonical
        stable id ``attestation_final_report_reminder:{instance_id}``,
        ``attestation_reminder_count == 1``, ``attestation_route ==
        "agent"``, the cap machinery present (``ATTESTATION_REMINDER_CAP
        == 2``), and — the FAMILY SEPARATION negative — ZERO Completion
        Check Note needles anywhere in the injected body.

  S2 — runtime zero-note + kind retirement contract
        Drive (b)/(d)-with-pending-shaped turn: live RUNNING child
        (``busy_descendants > 0``) → LCA busy trigger suppression →
        ``Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP`` → ZERO injected
        messages. Belt-and-braces: ZERO messages whose content matches
        any Completion Check Note shape. Direct retirement negative:
        ``_stable_id_for("completion_check_note", instance_id=...)``
        raises ``ValueError`` (the ``completion_check_note`` row was
        removed from the canonical id-format table; supported kinds are
        now exactly four: ``project``, ``shared_meta_kv``,
        ``attestation_nudge``, ``attestation_final_report_reminder``).

  S3 — whole-tree census (independent of the dev's pins)
        ``subprocess.run`` grep across the worktree for each retired
        token. Classify EVERY hit (LIVE daemon/frontend = FAIL;
        test negative-pin/witness = OK; docs/planning history = OK).
        Report the full hit→classification table.

Harness mirrors ``tests/integration/test_attestation_attest_first_e2e_
independent.py`` — stub manager + stub ledger + monkey-patched
resolvers + ``llm_judge_enabled=False`` (NO LLM HTTP, NO DB, NO
daemon). Real ``create_attestation_gate_node`` factory closure + real
``_stable_id_for`` + real ``attest_completion`` tool body.

Reference: ``.agents/shared/planning/leader-completion-attestation/
decisions.md`` D-ENTRY 2026-09-23 (incident b2f4dae9 retirement) +
``docs/setup.md`` LCA Phase 6.5 follow-up section.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from daemon.graph import (
    ATTESTATION_BUNDLED_REMINDER,
    ATTESTATION_FINAL_REPORT_REMINDER,
    ATTESTATION_REMINDER_CAP,
    ATTESTATION_REMINDER_COUNT_KEY,
    _FINAL_REPORT_REMINDER_TITLE,
    create_attestation_gate_node,
)
from daemon.services.attestation_gate import (
    GateSettings,
    build_gate_config,
)
from daemon.services.context_messages import _stable_id_for
from daemon.tools.attestation import (
    ATTEST_CLEAN_RESULT_TEXT,
    attest_completion,
    reset_attest_caller_content_for_tests,
    set_attest_caller_content,
)


# ─────────────────────────────────────────────────────────────────────────────
# Constants — family-separation evidence anchors
# ─────────────────────────────────────────────────────────────────────────────


# Stable instance id — the gate node and the synthetic states thread
# through this. The autouse resolver-reset fixture (below) ensures the
# resolver caches do not leak between tests.
INSTANCE_ID = "lcancheck-family-separation"

# Gate settings — window 3 (D4), deny_bound 3 (D5), mode "enforce"
# (D2 — only enforce writes the ledger; dry is a passive observer).
# The attested-allow path requires the enforce mode to fire the
# counter reset (trigger 1, ruling 1).
SETTINGS = GateSettings(mode="enforce", window=3, deny_bound=3)

# ``llm_judge_enabled=False`` keeps the test hermetic — no LLM HTTP,
# no judge stub. The gate's HOLD branch is independent of the LLM
# judge; setting the kill-switch off only short-circuits the
# fused-judge block.
_LLM_JUDGE_ENABLED = False

# Needles — the canonical Retirement Tokens from incident b2f4dae9.
# Each needle is a substring that, if found in LIVE ``daemon/`` or
# ``frontend/`` code, signals a Completion Check Note resurrection.
# Test negative-pin / witness occurrences (in ``tests/``) and docs /
# planning history (in ``.agents/shared/planning/``, ``docs/``) are
# ACCEPTABLE — they document the removal; only LIVE CODE references
# fail the gate.
_NEEDLES: tuple[str, ...] = (
    "COMPLETION_CHECK_NOTE_TEXT",
    "_make_completion_check_note_message",
    "_COMPLETION_CHECK_NOTE_TITLE",
    "_fused_hint_citation",
    "marker_hint_message",
    "completion_check_note",
    "Completion Check Note",
)

# Confirmation of the title — the attest-first Final Report Reminder's
# canonical title. This MUST appear in the HOLD-injected HumanMessage
# body so the family-separation positive is verifiable.
_FINAL_REPORT_REMINDER_TITLE_BODY_PREFIX = (
    "[SYSTEM CONTEXT: " + _FINAL_REPORT_REMINDER_TITLE + "]"
)

# Paths excluded from the census (build artifacts / VCS internals).
_CENSUS_EXCLUDES: tuple[str, ...] = (
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
)

# Retirement-witness docstrings — documented in
# ``daemon/services/context_messages.py::_stable_id_for`` docstring.
# These are the canonical historical-context blocks (D-entry
# 2026-09-23) and MUST be allowed in the LIVE daemon/ tree (the
# docstring is the only remaining reference to the retirement
# inside production code — the negative-pin in
# ``tests/unit/test_attestation_lca_note_removed.py`` uses the
# same regex-strip pattern). The regex covers the full witness
# block from ``completion_check_note`` kind REMOVED through the
# negative-pin reference line.
_WITNESS_DOCSTRING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "daemon/services/context_messages.py",
        re.compile(
            r"``completion_check_note`` kind REMOVED.*?"
            r"the negative census pin in.*?``\.",
            flags=re.DOTALL,
        ),
    ),
)

# Confirmation of the _stable_id_for error message — the kind is now
# rejected. The four surviving kinds are project, shared_meta_kv,
# attestation_nudge, attestation_final_report_reminder.
_STABLE_ID_SUPPORTED_KINDS: tuple[str, ...] = (
    "project",
    "shared_meta_kv",
    "attestation_nudge",
    "attestation_final_report_reminder",
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — minimal hermetic harness
# ─────────────────────────────────────────────────────────────────────────────


def _make_manager_stub(
    *,
    pending_children: int = 0,
    queued_wakeups: int = 0,
    live_descendants: int = 0,
    busy_descendants: int = 0,
) -> MagicMock:
    """MagicMock manager exposing the FOUR R2 facades +
    ``has_open_user_answer`` (the FIFTH legitimate-pending input).

    Defaults are ALL ZERO — the canonical "no work pending" baseline
    so the gate's deny/HOLD branches stay reachable unless a test
    explicitly arms one of the inputs.
    """
    m = MagicMock()
    m.count_pending_children.return_value = pending_children
    m.get_queued_or_expected_wakeups.return_value = queued_wakeups
    m.count_live_descendants.return_value = live_descendants
    m.count_busy_descendants.return_value = busy_descendants
    # ``has_open_user_answer`` MUST return the literal ``False`` — the
    # gate's duck-typing guard reads ``_answer_reader(instance_id) is
    # True`` (NOT just truthy) so a MagicMock auto-attr without a
    # configured return_value would NOT short-circuit the bypass.
    m.has_open_user_answer.return_value = False
    m.is_watchover_enabled.return_value = False
    m.is_question_pause_requested.return_value = False
    m.get_tree_ids_permanent.return_value = []
    m.enqueue_message = MagicMock()
    m.send_message = MagicMock()
    m.revive = MagicMock()
    m.config = None  # avoid load_config() fallback in judge path
    return m


def _make_ledger_stub(denied_count: int = 0) -> MagicMock:
    """MagicMock ledger — Phase 3 ledger with the four CRUD methods
    (D2 — only enforce mode writes). ``denied_count`` controls the
    value the stub's ``get`` returns so a test that wants to assert
    counter reset semantics threads a counter that started non-zero.
    ``set_metadata`` is also stubbed so the transient gate-exception
    marker (graph.py:5043) does not AttributeError if it fires."""
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
    the per-instance manager handle, instance id, settings, and gate
    config at GRAPH-BUILD time; the node receives ``state`` (and
    config) only at run time."""
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


def _attest_clean_tool_message(call_id: str) -> ToolMessage:
    """Invoke the REAL ``attest_completion`` tool body with the
    runtime hook set to a WHITESPACE-ONLY caller AIMessage (the
    clean-call shape). Returns a ``ToolMessage`` carrying
    ``ATTEST_CLEAN_RESULT_TEXT``.

    Whitespace-only sentinel: the runtime ContextVar MUST be NON-EMPTY
    (so ``if runtime_content:`` wins in the tool body) BUT strip to
    empty (so ``caller_content.strip()`` returns False). A single
    space satisfies both — the tool body treats whitespace-only
    callers as clean (production seam)."""
    try:
        set_attest_caller_content(
            AIMessage(
                content=" ",
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
    """Invoke the REAL ``attest_completion`` tool body with the runtime
    hook set to a NON-EMPTY caller AIMessage (the c5d9a38a bundled
    shape). Returns a ``ToolMessage`` carrying
    ``ATTEST_BUNDLED_RESULT_TEXT``."""
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
        assert result != ATTEST_CLEAN_RESULT_TEXT, (
            f"REAL attest_completion tool returned the CLEAN teacher "
            f"text for BUNDLED caller; got {result!r}"
        )
        return ToolMessage(
            content=result,
            tool_call_id=call_id,
            name="attest_completion",
        )
    finally:
        reset_attest_caller_content_for_tests()


def _delegated_ai(call_id: str = "family-sep-dispatch") -> AIMessage:
    """The delegation AIMessage — empty content + ``send_message``
    tool call. Anchors ``attestation_required=True`` (the delegation
    scan answers "did this mission dispatch children"). Without it the
    gate is OFF and the HOLD branch is unreachable."""
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


def _send_message_tool_message(call_id: str = "family-sep-dispatch") -> ToolMessage:
    """The send_message tool result — a ToolMessage shape the leader
    reads after the delegation. Content is a stub reply; only the
    tool_call_id link matters for the gate's delegation scan."""
    return ToolMessage(
        content="Delegation acknowledged; child will report.",
        tool_call_id=call_id,
        name="send_message",
    )


def _clean_attest_ai(call_id: str = "family-sep-attest-clean") -> AIMessage:
    """The CLEAN attest_call — empty content + ``attest_completion``
    tool call. The 2026-09-19 contract's required FIRST-half shape."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "attest_completion", "args": {}, "id": call_id}
        ],
    )


def _bundled_attest_ai(
    content: str, call_id: str = "family-sep-attest-bundled"
) -> AIMessage:
    """The BUNDLED attest_call — non-empty content +
    ``attest_completion`` tool call in ONE AIMessage (the c5d9a38a
    shape)."""
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
    """Clear kill-switch envs + reset the canonical resolver cache +
    the LLM-judge resolvers so each test sees a clean resolver state.

    Hermetic isolation per blueprint (c)."""
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
    """Pin caplog at INFO on the loggers that emit gate rows."""
    for logger_name in (
        "daemon.graph",
        "daemon.services.attestation_gate",
        "daemon.services.attestation_resolver_activation",
    ):
        caplog.set_level(logging.INFO, logger=logger_name)
    return caplog


# ─────────────────────────────────────────────────────────────────────────────
# S1 — HOLD → Final Report Reminder STILL injects (live)
# ─────────────────────────────────────────────────────────────────────────────


# Canonical Completion Check Note needles — the S1 negative
# assertion scans the HOLD-injected HumanMessage body for ANY of
# these. Family separation is verified: the Final Report Reminder
# body MUST NOT carry any Completion Check Note shape.
_COMPLETION_CHECK_NEEDLES = (
    "Completion Check Note",
    "_fused_hint_citation",
    "_make_completion_check_note_message",
    "COMPLETION_CHECK_NOTE_TEXT",
    "_COMPLETION_CHECK_NOTE_TITLE",
    "marker_hint_message",
)


def _assert_final_report_reminder_injected(
    result: dict[str, Any],
    *,
    instance_id: str,
    expected_reminder_kind: str,
    expected_reminder_body_substring: str,
) -> HumanMessage:
    """Assert the HOLD path injected the Final Report Reminder — the
    family-separation positive.

    Returns the injected HumanMessage so callers can drill into the
    body / id without re-querying the result dict.

    Asserts:
      * ``attestation_route == "agent"`` — HOLD routes back to leader
      * ``attestation_reminder_count == 1`` — counter incremented
      * exactly ONE HumanMessage in the returned messages list
      * the HumanMessage.id starts with the canonical
        ``attestation_final_report_reminder:`` prefix (F1 Shape A)
      * the body carries the ``[SYSTEM CONTEXT: Final Report
        Reminder]`` title (the canonical `_FINAL_REPORT_REMINDER_TITLE`)
      * the body carries ``expected_reminder_body_substring`` (the
        CLEAN or BUNDLED reminder body discriminator)
      * the body MUST NOT carry any Completion Check Note needle
        (family separation negative)
    """
    # Route hint MUST be "agent" — the gate routes back so the leader
    # can deliver the report.
    assert result.get("attestation_route") == "agent", (
        f"HOLD MUST route back to agent for re-issue; got "
        f"attestation_route={result.get('attestation_route')!r}"
    )
    # Counter incremented to 1 on HOLD (counter-independent of the
    # deny ledger; this is the per-mission reminder counter).
    assert result.get(ATTESTATION_REMINDER_COUNT_KEY) == 1, (
        f"per-mission reminder count MUST increment to 1 on HOLD; "
        f"got: {result.get(ATTESTATION_REMINDER_COUNT_KEY)!r}"
    )
    # Exactly one injected message.
    returned_messages = result.get("messages", [])
    assert len(returned_messages) == 1, (
        f"HOLD MUST inject exactly one reminder message; "
        f"got {len(returned_messages)}"
    )
    reminder = returned_messages[0]
    assert isinstance(reminder, HumanMessage), (
        f"HOLD reminder MUST be a HumanMessage; got "
        f"{type(reminder).__name__}"
    )
    # Stable id contract — the F1 Shape A supersede-in-place id
    # carries the ``attestation_final_report_reminder:`` prefix.
    expected_id_prefix = "attestation_final_report_reminder:"
    actual_id = getattr(reminder, "id", None)
    assert actual_id is not None and actual_id.startswith(expected_id_prefix), (
        f"HOLD reminder MUST carry the canonical stable id "
        f"{expected_id_prefix}{{instance_id}}; got id={actual_id!r}"
    )
    assert actual_id == f"{expected_id_prefix}{instance_id}", (
        f"HOLD reminder stable id MUST be exactly "
        f"{expected_id_prefix}{instance_id}; got {actual_id!r}"
    )
    # Body carries the canonical title.
    body = str(reminder.content or "")
    assert _FINAL_REPORT_REMINDER_TITLE_BODY_PREFIX in body, (
        f"HOLD reminder MUST carry the [{_FINAL_REPORT_REMINDER_TITLE!r}] "
        f"title; got body first 120 chars: {body[:120]!r}"
    )
    # Body carries the expected discriminator substring (CLEAN vs
    # BUNDLED). ``expected_reminder_kind`` is informational — the
    # substring check is the canonical discrimination.
    assert expected_reminder_body_substring in body, (
        f"HOLD reminder MUST carry the {expected_reminder_kind!r} "
        f"discriminator substring {expected_reminder_body_substring!r}; "
        f"got body: {body[:240]!r}"
    )
    # FAMILY SEPARATION NEGATIVE — ZERO Completion Check Note
    # needles in the HOLD-injected body. This is the core
    # family-isolation evidence: the attest-first Final Report
    # Reminder family is NOT contaminated by the retired Completion
    # Check Note family.
    body_lower = body.lower()
    for needle in _COMPLETION_CHECK_NEEDLES:
        assert needle.lower() not in body_lower, (
            f"HOLD reminder body MUST NOT carry the Completion Check "
            f"Note needle {needle!r}; got body: {body[:240]!r}"
        )
    return reminder


class TestS1HoldStillInjectsFinalReportReminder:
    """S1 — HOLD path → Final Report Reminder STILL injects (live).

    Two sub-scenarios drive the REAL ``create_attestation_gate_node``
    factory closure with the CLEAN-call shape and the c5d9a38a
    BUNDLED shape. Both routes are the attest-first HOLD contract:
    the FINAL AI is NOT a standalone text report, the gate resolves
    to ``Decision.HOLD``, the gate node injects exactly one
    HumanMessage carrying the canonical Final Report Reminder — the
    ``[SYSTEM CONTEXT: Final Report Reminder]`` title + the
    ``attestation_final_report_reminder:{instance_id}`` stable id +
    ``attestation_reminder_count == 1`` + ``attestation_route ==
    "agent"``. The injected body MUST NOT carry any Completion Check
    Note needle — that is the family-separation evidence."""

    def test_s1a_clean_attest_hold_injects_clean_final_report_reminder(
        self, gate_caplog
    ):
        """S1a — clean attest_call + short text-only ack → HOLD with
        the CLEAN Final Report Reminder."""
        node, manager, ledger = _make_real_gate_node(denied_count=0)

        attest_call_id = "family-sep-clean-attest"
        attest_msg = _clean_attest_ai(call_id=attest_call_id)
        attest_tool_msg = _attest_clean_tool_message(attest_call_id)
        short_ack = AIMessage(content="ok.")  # FINAL AI = short text-only ack

        state: dict[str, Any] = {
            "messages": [
                HumanMessage(content="please do it"),
                _delegated_ai(),  # anchors attestation_required=True
                _send_message_tool_message(),
                AIMessage(content="Hallucinated completion."),
                attest_msg,  # clean attest_call (turn 2)
                attest_tool_msg,  # REAL clean tool result
                short_ack,  # FINAL AI = short text-only ack
            ]
        }

        result = asyncio.run(
            node(state, config={"configurable": {"thread_id": INSTANCE_ID}})
        )
        assert result is not None, "gate node returned None"

        # ── Family-separation positive + negative assertions ──
        clean_substring = (
            "Attestation received. Deliver your full detailed "
            "final report now as your final message."
        )
        _assert_final_report_reminder_injected(
            result,
            instance_id=INSTANCE_ID,
            expected_reminder_kind="CLEAN",
            expected_reminder_body_substring=clean_substring,
        )

        # ── Belt and braces: CLEAN discriminator MUST NOT carry
        # BUNDLED-specific substring. ─────────────────────────
        reminder_body = str(result["messages"][0].content or "")
        assert (
            "tool-call message contained text"
            not in reminder_body
        ), (
            f"CLEAN HOLD MUST NOT carry the BUNDLED-specific "
            f"substring; got body: {reminder_body[:240]!r}"
        )

        # ── Ledger: HOLD is counter-INDEPENDENT (does NOT touch
        # the deny ledger). ──────────────────────────────────
        ledger.increment.assert_not_called()
        ledger.reset.assert_not_called()
        ledger.set_escalated_and_reset.assert_not_called()

        # ── Cap machinery present (the documented escape — the
        # third HOLD falls through to plain allow). ──────────
        assert ATTESTATION_REMINDER_CAP == 2, (
            f"ATTESTATION_REMINDER_CAP MUST be 2; got "
            f"{ATTESTATION_REMINDER_CAP}"
        )

        # ── Canonical log row: decision=hold with the FINAL
        # Report Reminder injection marker. ──────────────────
        log_text = "\n".join(r.getMessage() for r in gate_caplog.records)
        hold_rows = [
            line for line in log_text.splitlines() if "decision=hold" in line
        ]
        assert hold_rows, (
            "expected a ``decision=hold`` log row at the CLEAN HOLD "
            "turn-end; none found"
        )

    def test_s1b_bundled_attest_hold_injects_bundled_final_report_reminder(
        self, gate_caplog
    ):
        """S1b — bundled attest_call (c5d9a38a shape) → HOLD with
        the BUNDLED Final Report Reminder."""
        node, manager, ledger = _make_real_gate_node(denied_count=0)

        attest_call_id = "family-sep-bundled-attest"
        bundled_text = (
            "Bundled shape: short report content + attest_completion "
            "tool_call in ONE AIMessage (the c5d9a38a class). The "
            "runtime hook picks this up via the per-thread ContextVar, "
            "the tool body returns the BUNDLED teacher text, the gate "
            "classifies the final AI as a bundled shape, and decide() "
            "returns HOLD with the BUNDLED Final Report Reminder text. "
            "The leader must then re-issue the standalone report on the "
            "following turn as a clean AIMessage without tool_calls."
        )
        attest_msg = _bundled_attest_ai(
            bundled_text, call_id=attest_call_id
        )
        attest_tool_msg = _attest_bundled_tool_message(
            attest_call_id, bundled_text
        )

        state: dict[str, Any] = {
            "messages": [
                HumanMessage(content="please do it"),
                _delegated_ai(),  # anchors attestation_required=True
                _send_message_tool_message(),
                AIMessage(content="Hallucinated completion."),
                attest_msg,  # BUNDLED attest_call (turn 2)
                attest_tool_msg,  # REAL bundled tool result
                # FINAL AI = the SAME bundled message (no standalone
                # text report follows). The gate sees the last AI
                # as a bundled shape (text + attest tool_call) →
                # HOLD with the BUNDLED reminder.
            ]
        }

        result = asyncio.run(
            node(state, config={"configurable": {"thread_id": INSTANCE_ID}})
        )
        assert result is not None, "gate node returned None"

        # ── Family-separation positive + negative assertions ──
        bundled_substring = (
            "Attestation received, but your tool-call message "
            "contained text"
        )
        _assert_final_report_reminder_injected(
            result,
            instance_id=INSTANCE_ID,
            expected_reminder_kind="BUNDLED",
            expected_reminder_body_substring=bundled_substring,
        )

        # ── Belt and braces: BUNDLED discriminator MUST carry
        # the BUNDLED-specific substring (the c5d9a38a shape). ──
        reminder_body = str(result["messages"][0].content or "")
        assert (
            "Re-issue your full detailed final report now as its own"
            in reminder_body
        ), (
            f"BUNDLED HOLD MUST carry the re-issue substring; "
            f"got body: {reminder_body[:240]!r}"
        )
        # And MUST NOT carry the CLEAN-specific substring.
        assert (
            "Deliver your full detailed final report now as your final message"
            not in reminder_body
        ), (
            f"BUNDLED HOLD MUST NOT carry the CLEAN-specific "
            f"substring; got body: {reminder_body[:240]!r}"
        )

        # ── Ledger: HOLD is counter-INDEPENDENT. ──────────────
        ledger.increment.assert_not_called()
        ledger.reset.assert_not_called()
        ledger.set_escalated_and_reset.assert_not_called()

        # ── Cap machinery present. ────────────────────────────
        assert ATTESTATION_REMINDER_CAP == 2


# ─────────────────────────────────────────────────────────────────────────────
# S2 — runtime zero-note + kind retirement contract
# ─────────────────────────────────────────────────────────────────────────────


class TestS2RuntimeZeroNotePlusKindRetirement:
    """S2 — runtime proof the (b)/(d)-with-pending route emits ZERO
    Completion Check Notes + the ``completion_check_note`` kind is
    RETIRED from the canonical id-format table.

    Two sub-scenarios:

      S2a — live RUNNING child (busy_descendants > 0) + quiet tree
            otherwise + short text-only AIMessage + delegation in
            the tail → LCA busy trigger suppression →
            ``Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP`` → ZERO
            injected messages. The Completion Check Note surface is
            RETIRED end-to-end along the (b)/(d)-with-pending route
            (incident b2f4dae9, 2026-09-23).

      S2b — direct retirement negative: ``_stable_id_for(
            "completion_check_note", instance_id=...)`` raises
            ``ValueError`` — the kind was REMOVED from the canonical
            id-format table; supported kinds are now exactly four:
            ``project``, ``shared_meta_kv``, ``attestation_nudge``,
            ``attestation_final_report_reminder``.
    """

    def test_s2a_bd_with_pending_shaped_turn_zero_messages_injected(self):
        """S2a — drive a (b)/(d)-with-pending-shaped turn (live
        RUNNING child + judge not_complete is fine). The
        legitimate-pending-wakeup branch (decide() step 2) fires
        BEFORE the attested-split (step 3) so the gate resolves to
        ``Decision.ALLOWED_LEGITIMATE_PENDING_WAKEUP`` with ZERO
        side-effects on the message channel — NO nudge, NO hint,
        NO reminder.

        The path uses ``live_descendants > 0`` (work-bearing
        descendants, two-set semantics, incident b08f40fe) which
        triggers step (2) in ``decide()`` — the FIFTH (after
        pending_children + queued_or_expected_wakeups) legitimate-
        pending input. The retired (b)/(d)-with-pending hint surface
        is no longer reachable on this route because the route now
        resolves to allow via decide() step (2) BEFORE the marker /
        judge plumbing even runs.

        Belt-and-braces negative: even though no message is
        injected, scan the entire ``state["messages"]`` (the input)
        for any Completion Check Note shape — there MUST be NONE in
        the input either (no seeding bias)."""
        # Live RUNNING child → R2 legitimate-pending wakeup via
        # decide() step (2). NOTE: ``live_descendants`` (not
        # ``busy_descendants``) is the canonical input — the LCA
        # busy trigger suppression is for the (b)/(d) marker
        # path; ``live_descendants`` is the broader R2 allow that
        # fires BEFORE the marker plumbing.
        manager = _make_manager_stub(live_descendants=1)
        node, _manager, ledger = _make_real_gate_node(
            denied_count=0, manager=manager
        )

        state: dict[str, Any] = {
            "messages": [
                HumanMessage(content="please do it"),
                _delegated_ai(),  # anchors attestation_required=True
                _send_message_tool_message(),
                AIMessage(content="ok."),  # FINAL AI = short text-only
            ]
        }

        # Belt-and-braces negative: input messages MUST NOT carry
        # any Completion Check Note needle (no seeding bias).
        for idx, msg in enumerate(state["messages"]):
            msg_content = str(getattr(msg, "content", "") or "")
            msg_lower = msg_content.lower()
            for needle in _COMPLETION_CHECK_NEEDLES:
                assert needle.lower() not in msg_lower, (
                    f"input message[{idx}] MUST NOT carry the "
                    f"Completion Check Note needle {needle!r}; got "
                    f"content: {msg_content[:120]!r}"
                )

        result = asyncio.run(
            node(state, config={"configurable": {"thread_id": INSTANCE_ID}})
        )
        assert result is not None, "gate node returned None"

        # ZERO messages injected. The (b)/(d)-with-pending route
        # emits NOTHING post-retirement.
        injected = result.get("messages", [])
        assert injected == [], (
            f"(b)/(d)-with-pending-shaped turn MUST inject ZERO "
            f"messages; got {len(injected)}: {[type(m).__name__ for m in injected]!r}"
        )
        # No route-back (the R2 legitimate-pending-wakeup branch
        # routes to END, not back to the agent).
        assert result.get("attestation_route") in (None, "end"), (
            f"(b)/(d)-with-pending-shaped turn MUST NOT route back "
            f"to agent; got attestation_route="
            f"{result.get('attestation_route')!r}"
        )
        # The per-mission reminder counter MUST NOT increment on
        # the allow / legitimate-pending path (only HOLD increments).
        assert (
            result.get(ATTESTATION_REMINDER_COUNT_KEY) in (None, 0)
        ), (
            f"allow / legitimate-pending-wakeup MUST NOT touch the "
            f"per-mission reminder counter; got "
            f"{result.get(ATTESTATION_REMINDER_COUNT_KEY)!r}"
        )

        # Belt-and-braces scan: the input state had ZERO Completion
        # Check Note needles (asserted above). The output state
        # (which is just the input — the gate returns ZERO
        # messages) MUST ALSO have zero needles.
        all_messages = list(state["messages"]) + list(injected)
        for idx, msg in enumerate(all_messages):
            msg_content = str(getattr(msg, "content", "") or "")
            msg_lower = msg_content.lower()
            for needle in _COMPLETION_CHECK_NEEDLES:
                assert needle.lower() not in msg_lower, (
                    f"combined-state message[{idx}] MUST NOT carry "
                    f"the Completion Check Note needle {needle!r}; "
                    f"got content: {msg_content[:120]!r}"
                )

        # Ledger: allow / legitimate-pending-wakeup path does NOT
        # touch the deny ledger (the deny path was never entered).
        ledger.increment.assert_not_called()
        ledger.reset.assert_not_called()

    def test_s2b_stable_id_for_rejects_completion_check_note_kind(self):
        """S2b — direct retirement negative: the
        ``_stable_id_for`` canonical id-format table REJECTS the
        ``"completion_check_note"`` kind with a ``ValueError``
        naming the unknown kind. The four surviving kinds are
        ``project``, ``shared_meta_kv``, ``attestation_nudge``,
        ``attestation_final_report_reminder``."""
        # Belt and braces: the four surviving kinds MUST mint a
        # stable id WITHOUT raising — proves the table still works
        # for the attest-first family.
        project_id = _stable_id_for("project", instance_id="any-iid")
        assert project_id == "project:any-iid", (
            f"project kind MUST mint 'project:<iid>'; got {project_id!r}"
        )
        nudge_id = _stable_id_for("attestation_nudge", instance_id="any-iid")
        assert nudge_id == "attestation_nudge:any-iid", (
            f"attestation_nudge kind MUST mint 'attestation_nudge:<iid>'; "
            f"got {nudge_id!r}"
        )
        reminder_id = _stable_id_for(
            "attestation_final_report_reminder", instance_id="any-iid"
        )
        assert (
            reminder_id == "attestation_final_report_reminder:any-iid"
        ), (
            f"attestation_final_report_reminder kind MUST mint "
            f"'attestation_final_report_reminder:<iid>'; got "
            f"{reminder_id!r}"
        )
        kv_id = _stable_id_for("shared_meta_kv", context_key="ctx-x")
        assert kv_id == "kv:ctx-x", (
            f"shared_meta_kv kind MUST mint 'kv:<ctx>'; got {kv_id!r}"
        )

        # Direct retirement negative — the retired kind MUST raise.
        with pytest.raises(ValueError) as exc_info:
            _stable_id_for("completion_check_note", instance_id="any-iid")
        error_msg = str(exc_info.value)
        # The error MUST name the retired kind so operators can
        # debug a stale caller (the established F1 contract).
        assert "'completion_check_note'" in error_msg, (
            f"_stable_id_for ValueError MUST name the retired kind "
            f"'completion_check_note'; got error: {error_msg!r}"
        )
        # The error MUST enumerate the four surviving kinds (the
        # canonical id-format table message is the single source of
        # truth for supported kinds).
        for surviving_kind in _STABLE_ID_SUPPORTED_KINDS:
            assert surviving_kind in error_msg, (
                f"_stable_id_for ValueError MUST enumerate the "
                f"surviving kind {surviving_kind!r}; got error: "
                f"{error_msg!r}"
            )
        # The error MUST NOT enumerate the retired kind in the
        # supported-kinds list (it belongs in the unknown-kind
        # prefix only).
        supported_kinds_section = error_msg.split(
            "C0 mints ids only"
        )[-1] if "C0 mints ids only" in error_msg else error_msg
        assert (
            "completion_check_note" not in supported_kinds_section
        ), (
            f"_stable_id_for ValueError MUST NOT list "
            f"completion_check_note in the surviving-kinds set; "
            f"got error: {error_msg!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# S3 — whole-tree census (independent of the dev's pins)
# ─────────────────────────────────────────────────────────────────────────────


def _classify_path(relpath: str) -> str:
    """Classify a census hit by its path. Returns one of:

      * ``"daemon"`` — LIVE daemon code (FAIL if any needle hits here)
      * ``"frontend"`` — LIVE frontend code (FAIL if any needle hits
        here)
      * ``"test"`` — test code (OK — negative-pin / witness / docs)
      * ``"docs"`` — docs/setup.md or similar (OK — historical
        documentation of the removal)
      * ``"planning"`` — ``.agents/shared/planning/`` planning
        history (OK — historical record)
      * ``"tester_artifacts"`` — ``.agents/tester/`` test results,
        lessons, PACKS.md, memories (OK — historical record)
      * ``"other"`` — unknown (FAIL by default — operator must
        classify manually)

    The classification is intentionally conservative — only the four
    LIVE paths (``daemon/``, ``frontend/``) fail the gate; everything
    else is OK (docs / planning / tests / tester artifacts document
    the removal)."""
    p = relpath.replace("\\", "/")
    # Strip leading "./" if any.
    if p.startswith("./"):
        p = p[2:]
    if p.startswith("daemon/") or p == "daemon":
        return "daemon"
    if p.startswith("frontend/") or p == "frontend":
        return "frontend"
    if p.startswith("tests/") or p.startswith("test/") or p == "test":
        return "test"
    if p.startswith("docs/"):
        return "docs"
    if p.startswith(".agents/shared/planning/"):
        return "planning"
    if p.startswith(".agents/tester/"):
        return "tester_artifacts"
    return "other"


def _is_witness_docstring(relpath: str, line_body: str) -> bool:
    """Return True if this hit sits inside a known retirement-witness
    docstring block (e.g., the ``_stable_id_for`` docstring in
    ``daemon/services/context_messages.py``).

    The witness is identified by file + a regex strip on the file's
    content; the line_body check ensures we only match inside the
    stripped witness region. Same pattern as the dev's companion
    negative-pin test (``tests/unit/test_attestation_lca_note_
    removed.py::test_no_completion_check_note_anywhere_in_daemon``).
    """
    for witness_path, pattern in _WITNESS_DOCSTRING_PATTERNS:
        if relpath != witness_path:
            continue
        try:
            full_content = Path(relpath).read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeDecodeError):
            continue
        # Strip the witness block, then check whether the original
        # line_body still appears in the stripped content.
        stripped = pattern.sub("", full_content)
        if line_body.strip()[:80] not in stripped:
            # The hit line is INSIDE the witness block — strip
            # accepted.
            return True
    return False


def _run_census(needle: str, worktree_root: Path) -> list[tuple[str, str]]:
    """Run a grep for the needle across the worktree (excluding
    build artifacts) and return the list of ``(path, line)`` hits.

    Uses ``grep -rn`` (recursive, line numbers) so the hit list
    preserves the path + line for the classification table. Skips
    the configured exclude dirs via ``grep --exclude-dir``."""
    cmd: list[str] = ["grep", "-rn"]
    for excl in _CENSUS_EXCLUDES:
        cmd.extend(["--exclude-dir", excl])
    cmd.extend(["--", needle, "."])
    proc = subprocess.run(
        cmd,
        cwd=str(worktree_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    # grep exits 1 when there are zero matches — treat as empty hits
    # (NOT a failure of the census call itself).
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            f"grep for needle {needle!r} failed: rc={proc.returncode} "
            f"stderr={proc.stderr!r}"
        )
    hits: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        # grep -n output: ``./path:lineno:body`` (the leading ``./``
        # is grep's standard prefix when given a ``.`` search root).
        m = re.match(r"^(\./)?([^:]+):(\d+):(.*)$", line)
        if not m:
            continue
        rel = m.group(2)
        body = m.group(4)
        hits.append((rel, body))
    return hits


class TestS3WholeTreeCensus:
    """S3 — whole-tree census of the retired Completion Check Note
    surface. Independent of the dev's negative-pin
    ``test_attestation_lca_note_removed.py`` — this census scans the
    WHOLE WORKTREE (not just ``daemon/``) and classifies every hit.

    FAIL semantics:
      * ANY hit in LIVE ``daemon/`` or ``frontend/`` code (the
        resolver-retirement class — the mint site resurrection, the
        stable-id branch resurrection, the GateDecision field
        resurrection, etc.) is an immediate gate FAIL.
      * Hits in ``tests/``, ``docs/``, ``.agents/shared/planning/``,
        ``.agents/tester/`` (PACKS.md / RESULTS / LESSONS / memories)
        are OK — they are negative-pin witnesses, historical
        documentation, or planning history.

    The census is run via subprocess (real ``grep -rn``) so the
    test is independent of Python-side string parsing and mirrors
    the operator's standard forensic grep.
    """

    def test_s3_whole_tree_census(self, capsys):
        """Walk every retired needle across the worktree; classify
        every hit. LIVE ``daemon/`` or ``frontend/`` hits = FAIL."""
        worktree_root = Path(__file__).resolve().parents[2]

        # Capture for the human-readable classification table.
        table_lines: list[str] = []
        fail_hits: list[tuple[str, str, str]] = []  # (needle, path, line)

        for needle in _NEEDLES:
            hits = _run_census(needle, worktree_root)
            table_lines.append(
                f"\n── Needle: {needle!r}  (total hits: {len(hits)}) ──"
            )
            for path, body in hits:
                # Skip known retirement-witness docstring blocks
                # (e.g., the ``_stable_id_for`` docstring that
                # documents the removal). Same pattern as the
                # dev's companion negative-pin test.
                if _is_witness_docstring(path, body):
                    table_lines.append(
                        f"  [witness_docstring]  {path}: {body[:100]!r}"
                    )
                    continue
                classification = _classify_path(path)
                if classification in ("daemon", "frontend", "other"):
                    fail_hits.append((needle, path, body))
                table_lines.append(
                    f"  [{classification:>14}]  {path}: {body[:100]!r}"
                )

        # Render the classification table to stdout (the test
        # report picks it up via -s).
        print("\n".join(table_lines))

        # Hard gate — ZERO LIVE hits.
        if fail_hits:
            msg_lines = [
                "WHOLE-TREE CENSUS FAILED — live-code references to "
                "retired Completion Check Note surface:",
            ]
            for needle, path, body in fail_hits:
                msg_lines.append(
                    f"  NEEDLE {needle!r} → {path}: {body[:120]!r}"
                )
            msg_lines.append(
                "\nClassification rules: daemon/ + frontend/ = LIVE "
                "(FAIL); tests/ + docs/ + .agents/shared/planning/ + "
                ".agents/tester/ = OK (negative-pin / docs / planning "
                "history); other = FAIL (operator must classify)."
            )
            pytest.fail("\n".join(msg_lines))

        # Documented exclusion — ``daemon/services/context_messages.py``
        # is the SINGLE source-of-truth site whose comment-only
        # mentions of the retirement are ACCEPTABLE. We pin it here
        # so a future regression to LIVE code still fails LOUDLY
        # (the daemon/ classify-all path catches any resurrection).
        # (No additional assertions needed — the table above carries
        # the full evidence.)


# ─────────────────────────────────────────────────────────────────────────────
# Sanity pins — constants that anchor the test contract
# ─────────────────────────────────────────────────────────────────────────────


class TestFamilySeparationContractPins:
    """Sanity pins — the production constants this test depends on
    MUST be at their documented values. If ANY pin fails, the test
    envelope is broken (e.g., the removal collapsed the cap to 1 by
    accident, the kind list grew back, etc.) and the run is
    evidence-backed FAIL."""

    def test_attestation_reminder_cap_is_two(self):
        """``ATTESTATION_REMINDER_CAP`` is the documented escape
        from an infinite HOLD loop. Must remain 2 post-removal."""
        assert ATTESTATION_REMINDER_CAP == 2

    def test_final_report_reminder_title_unchanged(self):
        """The Final Report Reminder title MUST remain
        ``"Final Report Reminder"`` — the family-separation evidence
        anchor for the positive assertion (S1)."""
        assert _FINAL_REPORT_REMINDER_TITLE == "Final Report Reminder"

    def test_clean_and_bundled_reminder_constants_byte_identical(self):
        """The CLEAN and BUNDLED Final Report Reminder constants
        MUST be byte-identical to the documented texts. Belt and
        braces against accidental body-text drift."""
        assert (
            "Attestation received. Deliver your full detailed "
            "final report now as your final message."
            in ATTESTATION_FINAL_REPORT_REMINDER
        ), (
            f"ATTESTATION_FINAL_REPORT_REMINDER MUST carry the "
            f"canonical CLEAN body; got first 120 chars: "
            f"{ATTESTATION_FINAL_REPORT_REMINDER[:120]!r}"
        )
        assert (
            "Attestation received, but your tool-call message "
            "contained text"
            in ATTESTATION_BUNDLED_REMINDER
        ), (
            f"ATTESTATION_BUNDLED_REMINDER MUST carry the "
            f"canonical BUNDLED body; got first 120 chars: "
            f"{ATTESTATION_BUNDLED_REMINDER[:120]!r}"
        )

    def test_stable_id_supported_kinds_are_exactly_four(self):
        """The four surviving kinds are exactly ``project``,
        ``shared_meta_kv``, ``attestation_nudge``,
        ``attestation_final_report_reminder``. If a fifth kind is
        added (or one is removed), the kind-retirement contract is
        broken."""
        # Probe via the error message — the supported-kinds list
        # is the canonical message string.
        try:
            _stable_id_for("nonexistent_kind", instance_id="probe")
        except ValueError as exc:
            error_msg = str(exc)
        else:
            pytest.fail(
                "_stable_id_for MUST raise ValueError for unknown kinds"
            )
        # Extract the supported-kinds enumeration from the error.
        match = re.search(
            r"C0 mints ids only for\s+(.*?)\s*blocks",
            error_msg,
            flags=re.DOTALL,
        )
        assert match, (
            f"_stable_id_for ValueError MUST enumerate the "
            f"supported-kinds list; got error: {error_msg!r}"
        )
        kinds_section = match.group(1)
        # Strip quotes and whitespace; split on commas / "and".
        kinds = re.findall(r"'([^']+)'", kinds_section)
        assert sorted(kinds) == sorted(_STABLE_ID_SUPPORTED_KINDS), (
            f"supported-kinds list MUST be exactly "
            f"{sorted(_STABLE_ID_SUPPORTED_KINDS)!r}; got {sorted(kinds)!r}"
        )
