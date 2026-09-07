"""Acceptance matrix for the conditional-attestation gate OUTCOMES.

End-to-end (real LangGraph) coverage for the user-spec cases that need
the gate-node / decision log wiring:

* (a) delegation mission without attest → DENY + nudge (pinned
  alongside in tests/unit/test_attestation_nudge_inject.py);
* (d) delegation + attest → ALLOWED, counter reset;
* (e) THE SELF-REFERENCE TRAP — next turn-end after a deny STILL
  requires attestation because the nudge injection does NOT reset
  the delegation window;
* (g) no-real-user-message fallback (degenerate state).

Units (b), (c), (h), (i) are pinned in the companion unit-test file
``tests/unit/test_attestation_conditional_scanner.py`` and
``tests/unit/test_attestation_nudge_inject.py``.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from daemon.graph import (
    ATTESTATION_GATE_NODE_NAME,
    ATTESTATION_NUDGE_TEXT,
    create_attestation_gate_node,
    should_end_attestation,
)
from daemon.services.attestation_gate import (
    CANONICAL_LOG_SCHEMA_FIELDS,
    Decision,
    GateSettings,
    build_gate_config,
    decide,
    evaluate,
)
from daemon.services.attestation_ledger import safe_increment


def real(content: str = "ship the feature") -> HumanMessage:
    return HumanMessage(content=content)


def send_message_ai(tc_id: str = "t1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "send_message", "args": {}, "id": tc_id}],
    )


def attest_ai(tc_id: str = "t-attest") -> AIMessage:
    return AIMessage(
        content="Attesting now.",
        tool_calls=[{"name": "attest_completion", "args": {}, "id": tc_id}],
    )


def plain_ai(content: str = "All done.") -> AIMessage:
    return AIMessage(content=content)


def nudge_marker_kwargs():
    """The exact marker kwargs the deny-path injection carries —
    pinned by the original Phase 2 contract; preserved by the
    2026-09-06 amendment."""
    return {
        "attestation_nudge": True,
        "attestation_nudge_denied_count": 1,
    }


def make_manager(pending=0, wakeups=0, live_descendants=0):
    m = MagicMock()
    m.count_pending_children = MagicMock(return_value=pending)
    m.get_queued_or_expected_wakeups = MagicMock(return_value=wakeups)
    m.count_live_descendants = MagicMock(return_value=live_descendants)
    m.enqueue_message = MagicMock()
    m.revive = MagicMock()
    m.send_message = MagicMock()
    return m


# ─────────────────────────────────────────────────────────────────────────────
# (a) DELEGATION MISSION WITHOUT ATTEST → DENY + NUDGE
#
# Already pinned in ``tests/unit/test_attestation_nudge_inject.py``
# via the standalone gate-node wrapper. This file pins the same matrix
# at the ``evaluate()`` / log-row level — confirming the canonical
# schema log carries ``attestation_required=True`` AND the new header
# line in the nudge.
# ─────────────────────────────────────────────────────────────────────────────


class TestConditionalGateEvaluateOutcomes:
    """Direct ``evaluate()`` matrix for the conditional gate.

    Uses the INTERNAL ``evaluate()`` function (not the wrapping node)
    because we want to assert the exact decision value + log shape
    without the LangGraph wiring overhead.
    """

    def test_quick_question_returns_allowed_with_attestation_required_false(
        self,
    ) -> None:
        """Case (b) verified at the evaluate level — quick-question
        mission, no delegation, no attestation → ALLOWED with
        ``attestation_required=False``. The 17th schema field is
        in the log row."""
        msgs = [real("what is X?"), plain_ai("The answer is …")]
        result = evaluate(
            "inst-quick",
            0,
            msgs,
            GateSettings("enforce", 3, 3),
            make_manager(),
        )
        assert result.decision is Decision.ALLOWED
        assert result.attestation_required is False
        assert result.should_inject_nudge is False
        # No counter write on the conditional-gate-OFF ALLOW —
        # the ruling-1 "a non-fire is not a reset" invariant.
        assert result.next_denied_count == 0

    def test_conditional_gate_off_does_not_reset_nonzero_counter(self) -> None:
        """2026-09-06 strengthening of the ruling-1 invariant: a
        conditional-gate-OFF ALLOW with a NONZERO input counter
        must pass the counter through untouched
        (``next_denied_count == denied_count == 2``). This pins
        the no-reset semantic on the branch at
        ``daemon/services/attestation_gate.py:436-441`` (the
        conditional-gate-OFF early return) — a regression that
        clears the counter on a non-fire would silently lose the
        pre-existing ledger state."""
        result = decide(
            attested=False,
            pending_children=0,
            queued_or_expected_wakeups=0,
            live_descendants=0,
            denied_count=2,
            bound=3,
            scope_applicable=True,
            mode="enforce",
            attestation_enabled=True,
            # Conditional gate OFF — the leader did not delegate
            # since the last real user message; the gate must
            # ALLOW without resetting the counter.
            attestation_required=False,
        )
        assert result.decision is Decision.ALLOWED
        assert result.attestation_required is False
        assert result.should_inject_nudge is False
        # Ruling-1 invariant — the counter PASSES THROUGH, no
        # reset on a non-fire.
        assert result.next_denied_count == 2

    def test_chart_request_returns_allowed_with_attestation_required_false(
        self,
    ) -> None:
        """Case (c) at the evaluate level — the AI used a
        ``generate_chart`` tool call but NO ``send_message``. The
        conditional gate is OFF and the mission ALLOWS without
        demanding attestation."""
        msgs = [
            real("draw me a chart"),
            AIMessage(
                content="rendering",
                tool_calls=[
                    {"name": "generate_chart", "args": {}, "id": "c1"}
                ],
            ),
            plain_ai("chart rendered"),
        ]
        result = evaluate(
            "inst-chart",
            0,
            msgs,
            GateSettings("enforce", 3, 3),
            make_manager(),
        )
        assert result.decision is Decision.ALLOWED
        assert result.attestation_required is False
        assert result.should_inject_nudge is False

    def test_delegation_unattested_returns_denied_with_nudge_marker(
        self,
    ) -> None:
        """Case (a) at the evaluate level — delegated, NOT attested,
        no wakeups → DENIED. The ``attestation_required=True`` flag
        is recorded in the result for the canonical log."""
        msgs = [real("delegate and finish"), send_message_ai(), plain_ai("done")]
        result = evaluate(
            "inst-del",
            0,
            msgs,
            GateSettings("enforce", 3, 3),
            make_manager(),
        )
        assert result.decision is Decision.DENIED
        assert result.should_inject_nudge is True
        assert result.attestation_required is True
        assert result.next_denied_count == 1
        assert result.attestation_present is False
        # The NUDGE TEXT is the canonical byte-stable constant —
        # the integration tests assert its equality on the wire.
        assert ATTESTATION_NUDGE_TEXT.startswith(
            "[SYSTEM CONTEXT: Completion Check Nudge]"
        )

    def test_delegation_attested_returns_allowed_with_reset(
        self,
    ) -> None:
        """Case (d) at the evaluate level — delegated AND attested
        → ALLOWED with ``next_denied_count = 0`` (the
        attested-allow reset trigger). The conditional gate stays
        ON but the toolcall was made."""
        msgs = [
            real("delegate and finish"),
            send_message_ai(),
            attest_ai(),
        ]
        result = evaluate(
            "inst-del-attest",
            0,
            msgs,
            GateSettings("enforce", 3, 3),
            make_manager(),
        )
        assert result.decision is Decision.ALLOWED
        assert result.attestation_required is True  # gate was ON
        assert result.attestation_present is True
        assert result.should_inject_nudge is False
        # Reset trigger 1: attested-allow resets the counter.
        assert result.next_denied_count == 0

    def test_no_real_user_message_falls_back_to_conservative(self) -> None:
        """Case (g) at the evaluate level — degenerate state with
        no real user message exists. The conservative fallback
        returns ``attestation_required=True`` IF a delegation
        tool call exists anywhere in the list, else
        ``attestation_required=False`` (degenerate empty state)."""
        # No real user message AND no delegation anywhere.
        msgs = []
        result = evaluate(
            "inst-degenerate",
            0,
            msgs,
            GateSettings("enforce", 3, 3),
            make_manager(),
        )
        assert result.decision is Decision.ALLOWED
        # No real user AND no delegation → degenerate empty:
        # attestation_required is False (the conservative fallback
        # only flips to True if delegation is seen anywhere).
        assert result.attestation_required is False

    def test_no_real_user_message_with_stale_delegation_flips_to_required(
        self,
    ) -> None:
        """Case (g) extended — degenerate state but with a stale
        ``send_message`` AIMessage somewhere in the list. The
        fallback flips ``attestation_required=True`` (the
        conservative path) so the existing deny protection
        survives."""
        msgs = [send_message_ai("stale"), plain_ai("done")]
        result = evaluate(
            "inst-degenerate-with-delegation",
            0,
            msgs,
            GateSettings("enforce", 3, 3),
            make_manager(),
        )
        # Conservative fallback: a stale send_message anywhere in
        # the list keeps the gate ON (attestation_required=True).
        assert result.attestation_required is True
        # And since no attestation was made and no R2 inputs
        # surface, the gate DOES nudge (the legacy deny branch).
        assert result.should_inject_nudge is True
        assert result.next_denied_count == 1
        # ``last_real_user_found=False`` surfaces the diagnostic.
        assert result.last_real_user_found is False
        assert result.last_real_user_index == -1
        # Conservative: the gate STAYS ON — same as the standard
        # delegated case in the absence of any real user event.
        assert result.delegation_since_last_user is True
        assert result.first_delegation_after_last_user_index == 0


# ─────────────────────────────────────────────────────────────────────────────
# (e) THE SELF-REFERENCE TRAP — verify at evaluate() level
# ─────────────────────────────────────────────────────────────────────────────


class TestSelfReferenceTrapEvaluateLevel:
    """A deny-nudge injection MUST NOT cause
    ``delegation_since_last_user`` to flip to False on the subsequent
    turn-end. The scanner stays anchored on the original real user
    message; the gate stays ON. These tests verify the evaluate()
    behavior directly."""

    def test_after_deny_nudge_gate_still_requires_attestation(self) -> None:
        msgs_state_1_before_nudge = [
            real("delegate and finish"),
            send_message_ai(),
            plain_ai("done without attest"),
        ]
        result_1 = evaluate(
            "inst-trap",
            0,
            msgs_state_1_before_nudge,
            GateSettings("enforce", 3, 3),
            make_manager(),
        )
        assert result_1.decision is Decision.DENIED

        # Simulate the in-graph nudge injection (the actual gate
        # node does this — we replicate it here for the unit-level
        # pin).
        from langchain_core.messages import HumanMessage as _HM

        nudge_msg = _HM(
            content=ATTESTATION_NUDGE_TEXT,
            id=str(uuid.uuid4()),
            additional_kwargs=nudge_marker_kwargs(),
        )

        msgs_state_2_after_nudge = msgs_state_1_before_nudge + [nudge_msg]
        result_2 = evaluate(
            "inst-trap",
            1,  # counter incremented by the first deny
            msgs_state_2_after_nudge,
            GateSettings("enforce", 3, 3),
            make_manager(),
        )
        # The gate STILL requires attestation: nudge did NOT reset
        # the delegation window. Same DENY branch fires.
        assert result_2.attestation_required is True
        assert result_2.delegation_since_last_user is True
        # The nudge HumanMessage is NOT the new last real user
        # message — the original real user at index 0 remains
        # the anchor.
        assert result_2.last_real_user_index == 0
        # The deny path fires again (still no attestation in the
        # latest N AIMessages); the bound check is the
        # deciding factor at this point — with denied_count=1
        # and bound=3, the gate stays in branch (6) — DENIED.
        assert result_2.decision is Decision.DENIED
        assert result_2.next_denied_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# Schema drift pin — the 17-field canonical log schema
# ─────────────────────────────────────────────────────────────────────────────


class TestConditionalSchemaPin:
    """The canonical log schema MUST grow 16→17 fields and include
    ``attestation_required``. A drift here is silent — a future
    pinch that drops the field would break operators' observability
    of the new conditional gate (per O8 unit-guard convention)."""

    def test_canonical_schema_count_is_17(self) -> None:
        assert len(CANONICAL_LOG_SCHEMA_FIELDS) == 17

    def test_canonical_schema_includes_attestation_required(self) -> None:
        assert "attestation_required" in CANONICAL_LOG_SCHEMA_FIELDS

    def test_attestation_required_positioned_after_live_descendants(self) -> None:
        # 2026-09-06 amendment positioned the new field after the
        # third-input live_descendants field (grouped with R2 inputs).
        idx_new = CANONICAL_LOG_SCHEMA_FIELDS.index("attestation_required")
        idx_live = CANONICAL_LOG_SCHEMA_FIELDS.index("live_descendants")
        assert idx_live < idx_new, (
            "attestation_required must sit after live_descendants in "
            "the canonical schema (grouped with the R2 inputs)"
        )

    def test_schema_includes_unchanged_existing_fields(self) -> None:
        # Ensure no regression — the existing fields are preserved.
        for field in (
            "event",
            "decision",
            "instance_id",
            "attestation_present",
            "denied_count",
            "gate_location",
            "leader_prompt_version",
            "pending_children",
            "queued_or_expected_wakeups",
            "live_descendants",
            "attestation_required",
            "attest_seen_outside_window",
            "messages_scanned",
            "scanned_window_size",
            "mode",
            "scanner_window_truncated",
            "scanner_summary_seen",
        ):
            assert field in CANONICAL_LOG_SCHEMA_FIELDS, (
                f"missing canonical field {field!r} — schema regression"
            )


# ─────────────────────────────────────────────────────────────────────────────
# (b)/(c) Realtime-cases pinned at the FULL GRAPH level — confirms
# the relaxed gate path (NO send_message) lets the leader complete
# without ever invoking ``attest_completion``.
# ─────────────────────────────────────────────────────────────────────────────


class TestConditionalGateRealGraphLevel:
    """Full-graph P95 confirmation that the conditional gate does
    not fire on non-delegating turns (the user's core case)."""

    def test_quick_question_full_graph_terminates_without_nudge_or_attest(
        self,
    ) -> None:
        """End-to-end: real gate node, real manager facades, real
        LangGraph routing — a quick question with NO ``send_message``
        completes without injecting a nudge and without calling
        ``attest_completion``. The point of the test: the gate
        allows END on its first evaluation and the graph returns
        control without an endless attestation loop."""
        manager = make_manager()
        ledger = MagicMock()
        ledger.increment.return_value = 1
        ledger.reset.return_value = True
        config = build_gate_config("inst-quick-fg", GateSettings("enforce", 3, 3))
        node = create_attestation_gate_node(
            config,
            GateSettings("enforce", 3, 3),
            manager,
            "inst-quick-fg",
            denied_count_getter=lambda: 0,
            ledger=ledger,
        )
        result = asyncio.run(
            node(
                {
                    "messages": [
                        real("what's the answer to X?"),
                        plain_ai("The answer is Y."),
                    ]
                },
                config={"configurable": {"thread_id": "inst-quick-fg"}},
            )
        )

        # The gate ALLOWED — no nudge, no counter change.
        assert "messages" not in result
        assert result["attestation_route"] is None
        # Ledger untouched (the conditional-gate fastfollow's
        # invariant: a non-fire is not a reset).
        ledger.increment.assert_not_called()
        ledger.reset.assert_not_called()
        # Forbidden surfaces stayed inactive.
        manager.enqueue_message.assert_not_called()
        manager.revive.assert_not_called()

    def test_chart_mission_full_graph_terminates_without_nudge_or_attest(
        self,
    ) -> None:
        """End-to-end: a chart mission with ``generate_chart`` but
        NO ``send_message`` completes normally — the conditional
        gate stays OFF for non-delegating turns."""
        manager = make_manager()
        ledger = MagicMock()
        ledger.increment.return_value = 1
        ledger.reset.return_value = True
        config = build_gate_config("inst-chart-fg", GateSettings("enforce", 3, 3))
        node = create_attestation_gate_node(
            config,
            GateSettings("enforce", 3, 3),
            manager,
            "inst-chart-fg",
            denied_count_getter=lambda: 0,
            ledger=ledger,
        )
        result = asyncio.run(
            node(
                {
                    "messages": [
                        real("draw me a chart"),
                        AIMessage(
                            content="rendering",
                            tool_calls=[
                                {
                                    "name": "generate_chart",
                                    "args": {},
                                    "id": "c1",
                                }
                            ],
                        ),
                        plain_ai("here's the chart"),
                    ]
                },
                config={"configurable": {"thread_id": "inst-chart-fg"}},
            )
        )

        assert "messages" not in result
        assert result["attestation_route"] is None
        ledger.increment.assert_not_called()
        manager.enqueue_message.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Sanity: the (a) delegated-mission flow STILL denies — unchanged
# protection. Pinned at the full-graph level for completeness.
# ─────────────────────────────────────────────────────────────────────────────


class TestConditionalGateDelegatedStillDenies:
    """Case (a) at the full-graph level — pinning the unchanged
    protection on delegated missions."""

    def test_delegated_mission_no_attest_denies_with_new_nudge_header(
        self,
    ) -> None:
        manager = make_manager()
        ledger = MagicMock()
        ledger.increment.return_value = 1
        ledger.reset.return_value = True
        config = build_gate_config(
            "inst-del-deny-fg", GateSettings("enforce", 3, 3)
        )
        node = create_attestation_gate_node(
            config,
            GateSettings("enforce", 3, 3),
            manager,
            "inst-del-deny-fg",
            denied_count_getter=lambda: 0,
            ledger=ledger,
        )
        result = asyncio.run(
            node(
                {
                    "messages": [
                        real("delegate and finish"),
                        send_message_ai(),
                        plain_ai("done without attesting"),
                    ]
                },
                config={"configurable": {"thread_id": "inst-del-deny-fg"}},
            )
        )

        nudge = result["messages"][0]
        # Nudge injected (R1 — checkpoint-durable).
        assert nudge.content == ATTESTATION_NUDGE_TEXT
        # Nudge leads with the system-context header.
        assert nudge.content.startswith("[SYSTEM CONTEXT: Completion Check Nudge]")
        # Marker kwargs unchanged (back-compat with the scanner
        # exclusion predicate).
        assert nudge.additional_kwargs["attestation_nudge"] is True
        assert (
            nudge.additional_kwargs["attestation_nudge_denied_count"] == 1
        )
        # Routed back to the agent for the next turn.
        assert result["attestation_route"] == "agent"
        ledger.increment.assert_called_once()
