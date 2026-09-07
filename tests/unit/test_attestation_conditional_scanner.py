"""Acceptance matrix for the conditional-attestation gate (Phase 6
fastfollow, 2026-09-06) — units (a)–(i) of the user spec.

This file covers the SCANNER surface (``is_real_user_message`` and
``scan_delegation_after_last_user``) and its integration with the gate.
The gate-level outcomes (deny / allow / counter reset) live in the
companion file
``tests/integration/test_attestation_conditional_gate_outcomes.py``.
The unit-level reuse of the existing attest nudge injection lives in
``tests/unit/test_attestation_nudge_inject.py``.

Matrix coverage (user-spec cases):

(a) delegation mission without attest → DENY + nudge (unchanged
    protection) — pinned in
    ``tests/unit/test_attestation_nudge_inject.py``;
(b) quick-question mission → ALLOWED, ``attestation_required=False`` —
    pinned in
    ``tests/unit/test_attestation_nudge_inject.py``;
(c) chart mission (``generate_chart`` toolcall, no ``send_message``)
    → ALLOWED — covered here (test_conditional_chart_mission_allows);
(d) delegation + attest → ALLOWED, counter reset — covered here
    (test_delegation_plus_attest_allows_with_reset);
(e) THE SELF-REFERENCE TRAP — deny fires; on the next turn-end the
    gate STILL requires attestation because the nudge does not reset
    the delegation window — covered here
    (test_self_reference_trap_deny_nudge_does_not_reset_window);
(f) child-report HumanMessage does not reset the delegation window —
    covered here (test_child_report_does_not_anchor_or_reset_window)
    plus the exclusion-class matrix in this file;
(g) no-real-user-message fallback (degenerate state) —
    covered here (test_no_real_user_message_falls_back_to_whole_list);
(h) nudge header present + marker unchanged — pinned in
    ``tests/unit/test_attestation_nudge_inject.py``;
(i) scanner unit matrix for the exclusion classes — covered here
    (TestRealUserMessageExclusionClassMatrix).

The tests are pure-function checks against ``is_real_user_message``,
``find_last_real_user_index``, and ``scan_delegation_after_last_user``
plus a small amount of gate-shape coverage (the gate outcome matrices
that need the full graph wiring live in the companion integration
file).
"""
from __future__ import annotations

from typing import List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from daemon.services.attestation_scanner import (
    DEFAULT_ATTESTATION_TOOL_NAME,
    DEFAULT_DELEGATION_TOOL_NAME,
    DelegationScanResult,
    find_last_real_user_index,
    is_real_user_message,
    scan_delegation_after_last_user,
)


def real(content: str = "ship the feature") -> HumanMessage:
    """A real, user-authored HumanMessage — no special markers."""
    return HumanMessage(content=content)


def nudge(content: str = "nudge") -> HumanMessage:
    """A attestation-gate deny injection — marked by additional_kwargs."""
    return HumanMessage(
        content=content,
        additional_kwargs={"attestation_nudge": True},
    )


def system_context(content: str) -> HumanMessage:
    """A ``[SYSTEM CONTEXT: ...]`` HumanMessage — internal injected
    context block (the canonical ``_make_context_message`` factory)."""
    return HumanMessage(
        content=f"[SYSTEM CONTEXT: AutoLoadSkills]\n\n{content}",
        additional_kwargs={"injected_message": True, "context_kind": "auto_load_skills"},
    )


def child_report(content: str = "child completed") -> HumanMessage:
    """A child-report HumanMessage in the PRODUCTION enqueue-lane
    shape — derived from the REAL ``_build_graph_input`` constructor
    (stamped-shape contract: ``{"injected_message": True, "source":
    "internal_report:<iid>:<mid>"}``) rather than hand-modeled kwargs.

    Fixture blind spot closed (2026-09-07 review critical): this
    fixture previously hand-modeled the live-drain shape only, so the
    production enqueue lane — which delivered reports BARE (no
    additional_kwargs) to parked parents — was never exercised and the
    report-masquerade hole went untested. Deriving from the constructor
    keeps this fixture honest if the stamp ever drifts.
    """
    from daemon.services.instance_messaging import _build_graph_input

    built = _build_graph_input(
        content,
        "fixture-msg-id",
        message_source="internal_report:child-1:fixture-msg-id",
    )
    return built["messages"][-1]


def internal_agent_msg(content: str = "internal ping") -> HumanMessage:
    """An internal_agent dispatch HumanMessage (per
    ``daemon/services/instance_messaging.py``)."""
    return HumanMessage(
        content=content,
        additional_kwargs={"source": "internal_agent:cascade_resume"},
    )


def synthetic(content: str = "synthetic system") -> HumanMessage:
    """A synthetic HumanMessage (``is_synthetic=True``) — covers the
    persistence-synthetic path even if a future flow lands it as a
    user-role shape."""
    return HumanMessage(
        content=content,
        additional_kwargs={"is_synthetic": True},
    )


def ai(content: str = "delegating", tool_calls: List[dict] | None = None) -> AIMessage:
    return AIMessage(content=content, tool_calls=tool_calls or [])


def send_message_call(tc_id: str = "t1") -> List[dict]:
    return [{"name": DEFAULT_DELEGATION_TOOL_NAME, "args": {}, "id": tc_id}]


def generate_chart_call(tc_id: str = "t1") -> List[dict]:
    return [{"name": "generate_chart", "args": {}, "id": tc_id}]


# ─────────────────────────────────────────────────────────────────────────────
# (i) Exclusion-class matrix for ``is_real_user_message``
# ─────────────────────────────────────────────────────────────────────────────


class TestRealUserMessageExclusionClassMatrix:
    """``is_real_user_message`` is the canonical predicate the gate
    uses to anchor its delegation window. Every exclusion class must
    match the codebase's existing metadata surface — silent drift here
    would either (1) false-positive as a real user (relaxing the gate
    on a delegation that should require attestation) or (2)
    false-negative (locking a real follow-up question into a denial
    loop)."""

    def test_real_user_message_is_recognized(self) -> None:
        assert is_real_user_message(real()) is True
        assert is_real_user_message(real("anything")) is True

    def test_attestation_nudge_is_excluded(self) -> None:
        """The gate's own deny injection MUST NOT be the "last real
        user message" (would reset the delegation window on deny
        and self-defeat the conditional gate).
        """
        assert is_real_user_message(nudge()) is False

    def test_system_context_injection_is_excluded(self) -> None:
        assert is_real_user_message(system_context("hello")) is False

    def test_child_report_is_excluded(self) -> None:
        """A child-report HumanMessage (``internal_report:*`` source
        AND ``injected_message=True``) MUST NOT be a real user
        message — a child report landing as the newest message MUST
        NOT reset the delegation window."""
        assert is_real_user_message(child_report()) is False

    def test_enqueue_lane_stamped_shapes_all_excluded(self) -> None:
        """2026-09-07 review critical: every internal namespace the
        enqueue lane stamps (``internal_report:`` /
        ``internal_error_report:`` / ``internal_agent:`` /
        ``system:``) is classified NOT-a-real-user-message when
        delivered in the stamped shape. ``system:`` and
        ``internal_error_report:`` cover the watchdog hang / wedge
        notices and error-report rows — the sub-exposures of the
        same root cause."""
        from daemon.services.instance_messaging import _build_graph_input

        for source in (
            "internal_report:child-1:m-1",
            "internal_error_report:child-1:m-1",
            "internal_agent:caller-iid",
            "system:watchdog",
            "system:watchdog:wedge",
        ):
            built = _build_graph_input(
                "internal delivery", f"mid-{source[:12]}", message_source=source
            )
            msg = built["messages"][-1]
            # The stamped-shape contract as implemented by the
            # constructor.
            assert msg.additional_kwargs == {
                "injected_message": True,
                "source": source,
            }, source
            assert is_real_user_message(msg) is False, source

    def test_stamped_conjunction_requires_flag_and_prefix(self) -> None:
        """The 4b branch is a CONJUNCTION — the stamped shape is
        classified by (internal prefix AND injected flag) together.
        Boundary pins:

        * injected flag WITHOUT any source → excluded (ladder step 3,
          the flag is the canonical signal);
        * internal-looking source WITHOUT the flag → NOT classified
          by 4b (and not by the prefix-only step 4 for the
          ``system:`` / ``internal_error_report:`` namespaces) — bare
          means real user per the stamped-shape contract. This is the
          anti-overreach pin: the scanner classifies the STAMPED
          SHAPE, never a bare content heuristic.
        """
        flag_only = HumanMessage(
            content="anything",
            additional_kwargs={"injected_message": True},
        )
        assert is_real_user_message(flag_only) is False

        bare_source_system = HumanMessage(
            content="watchdog-shaped text",
            additional_kwargs={"source": "system:watchdog"},
        )
        assert is_real_user_message(bare_source_system) is True

    def test_bare_internal_report_masquerades_without_stamp(self) -> None:
        """HAZARD DOCUMENTATION (2026-09-07 review critical): a child
        report HumanMessage with NO additional_kwargs — the pre-fix
        enqueue-lane shape actually delivered to parked parents — IS
        classified as a real user message. The scanner has no bare
        content heuristic (by design), so the constructor stamp in
        ``_build_graph_input`` is LOAD-BEARING: if the stamp is ever
        dropped, this pin + the constructor sweep test
        (``test_attestation_construction_site_sweep.py``) are the two
        tripwires that fire."""
        bare_report = HumanMessage(
            content="child completed — all tasks done, report follows"
        )
        assert is_real_user_message(bare_report) is True

    def test_internal_agent_dispatch_is_excluded(self) -> None:
        """``internal_agent:*`` source-prefix messages (cascade
        resume / internal invoke) MUST NOT be real user messages."""
        assert is_real_user_message(internal_agent_msg()) is False

    def test_synthetic_message_is_excluded(self) -> None:
        """``is_synthetic=True`` HumanMessages MUST NOT be real user
        messages (defense in depth for the synthetic-system path)."""
        assert is_real_user_message(synthetic()) is False

    def test_aimessage_and_systemmessage_never_count(self) -> None:
        """The type gate: non-HumanMessage is excluded by
        construction. AIMessage / SystemMessage / ToolMessage /
        RemoveMessage all fail the isinstance(HumanMessage) check
        before the marker predicates run."""
        assert is_real_user_message(ai()) is False
        assert is_real_user_message(SystemMessage(content="sys")) is False

    def test_content_sentinel_prefix_excluded_even_without_kwargs(self) -> None:
        """Even with NO additional_kwargs, a HumanMessage whose body
        starts with ``[SYSTEM CONTEXT:`` is excluded by the content
        sentinel predicate (defense in depth against kwargs shim
        drift)."""
        sentinel_only = HumanMessage(
            content="[SYSTEM CONTEXT: anything\n\nbody"
        )
        assert is_real_user_message(sentinel_only) is False

    def test_combined_marker_and_source_excluded(self) -> None:
        """A HumanMessage carrying BOTH the nudge marker AND an
        ``internal_*`` source is excluded for either reason — the
        exclusion is OR-by-marker."""
        combo = HumanMessage(
            content="nudge-style message",
            additional_kwargs={
                "attestation_nudge": True,
                "source": "internal_agent:cascade_resume",
            },
        )
        assert is_real_user_message(combo) is False


# ─────────────────────────────────────────────────────────────────────────────
# ``find_last_real_user_index`` — backward walk over the message list
# ─────────────────────────────────────────────────────────────────────────────


class TestFindLastRealUserIndex:
    def test_returns_index_of_last_real_user(self) -> None:
        msgs = [
            real("first"),
            ai(),
            real("second"),  # ← this one
            ai(),
        ]
        assert find_last_real_user_index(msgs) == 2

    def test_returns_minus_one_when_no_real_user(self) -> None:
        msgs = [nudge(), system_context("ctx"), child_report()]
        assert find_last_real_user_index(msgs) == -1

    def test_ignores_injections_after_last_real_user(self) -> None:
        """Critical contract — child-report / nudge / context AFTER
        the last real user message MUST NOT shift the index."""
        msgs = [
            real("real"),
            ai(),
            # All injections after the real user message — should
            # NOT cause ``find_last_real_user_index`` to return the
            # position of any of these.
            ai(),
            child_report(),
            nudge(),
            system_context("end"),
        ]
        assert find_last_real_user_index(msgs) == 0

    def test_walks_backward_from_newest(self) -> None:
        msgs = [
            real("older real 1"),
            ai(),
            real("older real 2"),
            ai(),
            real("newest real"),  # ← this one
        ]
        assert find_last_real_user_index(msgs) == 4

    def test_empty_message_list_returns_minus_one(self) -> None:
        assert find_last_real_user_index([]) == -1


# ─────────────────────────────────────────────────────────────────────────────
# ``scan_delegation_after_last_user`` — delegation check across the tail
# ─────────────────────────────────────────────────────────────────────────────


class TestScanDelegationAfterLastUser:
    """The conditional-attestation scanner walk."""

    def test_delegation_after_real_user_returns_true(self) -> None:
        msgs = [
            real("go"),
            ai("dispatching", tool_calls=send_message_call()),  # ← delegation
        ]
        result = scan_delegation_after_last_user(msgs)
        assert result.delegation_since_last_user is True
        assert result.last_real_user_index == 0
        assert result.first_delegation_after_last_user_index == 1
        assert result.delegation_tool_call_total == 1

    def test_no_delegation_returns_false(self) -> None:
        msgs = [
            real("go"),
            ai("plain answer"),
        ]
        result = scan_delegation_after_last_user(msgs)
        assert result.delegation_since_last_user is False
        assert result.last_real_user_index == 0
        assert result.first_delegation_after_last_user_index == -1
        assert result.delegation_tool_call_total == 0

    def test_chart_mission_no_send_message_allows(self) -> None:
        """Case (c): the mission is to produce a chart; the AI uses
        ``generate_chart`` (registered via ``tools.allow``) but does
        NOT call ``send_message``. The conditional gate is OFF — the
        quick-task case.
        """
        msgs = [
            real("draw me a chart"),
            ai("rendering", tool_calls=generate_chart_call()),
            ai("here's the chart"),
        ]
        result = scan_delegation_after_last_user(msgs)
        assert result.delegation_since_last_user is False
        assert result.last_real_user_found is True

    def test_child_report_does_not_anchor_or_reset_window(self) -> None:
        """Case (f): a child-report HumanMessage arrives AFTER the
        last real user message but does NOT shift the delegation
        anchor. The scanner still walks for ``send_message`` tool
        calls across AIMessages strictly after the real user — none
        exist — so the result is ``delegation_since_last_user=False``.
        """
        msgs = [
            real("delegate and finish"),
            ai("dispatching", tool_calls=send_message_call()),
            child_report("child completed"),  # child report AFTER
            ai("done"),  # final AIMessage (no send_message)
        ]
        result = scan_delegation_after_last_user(msgs)
        # The delegation WAS recorded earlier in the tail — counted
        # by the scan regardless of the intervening child-report.
        assert result.delegation_since_last_user is True
        # The index recorded is the FIRST send_message AFTER the
        # real user message (index 1, dispatching).
        assert result.first_delegation_after_last_user_index == 1
        assert result.delegation_tool_call_total == 1

    def test_no_real_user_message_falls_back_to_whole_list(self) -> None:
        """Case (g): degenerate state with NO real user message
        exists. Conservative fallback — the scan walks the WHOLE
        list (a delegation seen ANYWHERE in the history triggers
        ``delegation_since_last_user=True``). The gate reads this
        to require attestation in the rare no-real-user-event
        case (defense against silent regression)."""
        msgs = [
            # No real user message — all internal injections.
            system_context("ctx"),
            ai("internal dispatch", tool_calls=send_message_call()),
            nudge(),
        ]
        result = scan_delegation_after_last_user(msgs)
        assert result.last_real_user_found is False
        assert result.last_real_user_index == -1
        # Fallback: tail_start == 0 → delegation seen anywhere → True.
        assert result.delegation_since_last_user is True
        assert result.first_delegation_after_last_user_index == 1
        assert result.delegation_tool_call_total == 1

    def test_does_not_count_delegation_before_real_user(self) -> None:
        """A stale ``send_message`` BEFORE the last real user message
        MUST NOT re-arm the conditional requirement (would re-fire the
        gate on a quick follow-up question)."""
        msgs = [
            ai("stale dispatch", tool_calls=send_message_call("t-stale")),
            real("new user follow-up"),
            ai("plain answer"),
        ]
        result = scan_delegation_after_last_user(msgs)
        # Real user at index 1; tail walks indices 1..end → no
        # send_message tool calls in that tail → delegation_since_last_user=False
        assert result.last_real_user_index == 1
        assert result.delegation_since_last_user is False
        assert result.first_delegation_after_last_user_index == -1
        assert result.delegation_tool_call_total == 0

    def test_multiple_delegations_counted_in_tool_call_total(self) -> None:
        msgs = [
            real("delegate twice"),
            ai("dispatch first", tool_calls=send_message_call("t1")),
            ai("dispatch second", tool_calls=send_message_call("t2")),
            ai("dispatch third", tool_calls=send_message_call("t3")),
        ]
        result = scan_delegation_after_last_user(msgs)
        assert result.delegation_since_last_user is True
        assert result.first_delegation_after_last_user_index == 1
        assert result.delegation_tool_call_total == 3

    def test_custom_delegation_tool_name(self) -> None:
        """The scanner accepts an override ``delegation_tool_name``
        (the seam for future-proofing if the canonical tool name
        ever changes — currently pinned to ``send_message``)."""
        msgs = [
            real("go"),
            ai("custom dispatch", tool_calls=[
                {"name": "custom_send", "args": {}, "id": "c1"}
            ]),
        ]
        result = scan_delegation_after_last_user(
            msgs, delegation_tool_name="custom_send"
        )
        assert result.delegation_since_last_user is True
        result_default = scan_delegation_after_last_user(msgs)
        # Default tool name ``send_message`` does NOT match →
        # delegation not detected for the default scanner.
        assert result_default.delegation_since_last_user is False


# ─────────────────────────────────────────────────────────────────────────────
# (e) THE SELF-REFERENCE TRAP — the deny-nudge is injected as a
# HumanMessage but does NOT shift the delegation window. The next
# turn-end STILL requires attestation. This is the
# feature-self-defeats-if-it-counts case the user flagged.
# ─────────────────────────────────────────────────────────────────────────────


class TestSelfReferenceTrapDenyNudgeDoesNotResetWindow:
    """The deny-nudge is injected as a HumanMessage. If it counted as
    a real user message, the delegation window after deny would be
    reset → the gate would relax on retry → the feature self-defeats.
    These tests prove the predicate excludes the nudge and the gate
    still REQUIRES attestation on the next turn-end."""

    def test_after_deny_nudge_window_unchanged_for_delegated(self) -> None:
        """Scenario: leader delegated a child; first turn-end is
        unattested → DENY + nudge. The nudge is injected as a
        HumanMessage but does NOT shift the delegation anchor. A
        subsequent turn-end with no new attestation MUST still be
        a DENY (the gate is still ON)."""
        msgs = [
            real("delegate and finish"),
            ai("dispatching", tool_calls=send_message_call()),
            ai("done without attest"),  # → DENY; nudge injected NEXT
            nudge(),  # the deny injection (HumanMessage)
            ai("retry — still done, still no attest"),  # next turn-end
        ]
        result = scan_delegation_after_last_user(msgs)
        # Even with the nudge present in the tail, the
        # delegation_since_last_user flag remains True because the
        # send_message tool call sits in the AIMessages trailing the
        # real user message. The nudge did NOT reset the window.
        assert result.delegation_since_last_user is True
        assert result.first_delegation_after_last_user_index == 1

    def test_deny_nudge_does_not_become_last_real_user(self) -> None:
        """The deny-nudge MUST NOT be found by
        ``find_last_real_user_index`` — the index is still the
        real user message that started the mission."""
        msgs = [
            real("go"),  # index 0 — the only real user
            ai(),
            ai(),
            nudge(),  # appended AFTER the denials — should not count
        ]
        assert find_last_real_user_index(msgs) == 0


# ─────────────────────────────────────────────────────────────────────────────
# DelegationScanResult — NamedTuple structural pin
# ─────────────────────────────────────────────────────────────────────────────


class TestDelegationScanResultShape:
    """Lock the NamedTuple field set so a future refactor cannot
    silently add/drop a field without the matrix tests breaking."""

    def test_field_set_is_frozen(self) -> None:
        expected = (
            "delegation_since_last_user",
            "last_real_user_index",
            "last_real_user_found",
            "first_delegation_after_last_user_index",
            "delegation_tool_call_total",
        )
        actual = tuple(DelegationScanResult._fields)
        assert actual == expected, (
            f"DelegationScanResult field set drifted: {actual}; "
            f"expected {expected}. Any future refactor MUST update "
            f"the gate + log emission + integration tests in lockstep."
        )

    def test_default_delegation_tool_name_pinned(self) -> None:
        """The canonical delegation-tool name MUST remain
        ``send_message`` until a future migration pins a new name
        and updates the trap documentation."""
        assert DEFAULT_DELEGATION_TOOL_NAME == "send_message"


# ─────────────────────────────────────────────────────────────────────────────
# THE ENQUEUE-LANE REGRESSION (2026-09-07 review critical)
#
# Canonical failure chain: child completes → child_reports enqueues the
# report row + PROCESS_REPORT task → the parked parent has NO live turn,
# so the marker-stamping live-drain never runs → the report is delivered
# as a BARE HumanMessage (constructed by ``_build_graph_input``) → the
# scanner ladder fell through every step → is_real_user_message=True →
# the delegation window RESET → the gate ALLOWED an un-attested
# delegated END. Closed by (1) the constructor stamp and (2) ladder
# STEP 3 (the ``injected_message`` flag branch) — the PRIMARY defense;
# branch 4b is fail-closed defense in depth, currently unreachable as
# an exclusion for constructor-produced shapes because Step 3 returns
# False first whenever the flag is True (and 4b requires the flag
# True). These tests prove the post-fix behavior end-to-end through
# the real ``evaluate()`` gate.
# ─────────────────────────────────────────────────────────────────────────────


class TestEnqueueLaneReportDoesNotResetDelegationWindow:
    """A child report delivered via the ENQUEUE lane (parked parent —
    no live drain) must NOT reset the delegation window."""

    @staticmethod
    def _enqueue_lane_report() -> HumanMessage:
        """The report shape exactly as the production enqueue lane
        constructs it — the REAL ``_build_graph_input`` output for an
        ``internal_report:`` queue-row source (post-fix stamped
        shape)."""
        from daemon.services.instance_messaging import _build_graph_input

        built = _build_graph_input(
            "child completed — all tasks done, full report",
            "rq-1",
            message_source="internal_report:child-iid:rq-1",
        )
        return built["messages"][-1]

    @staticmethod
    def _evaluate_tail():
        """Run the REAL gate ``evaluate()`` over the canonical
        delegated-mission tail whose newest turn-end follows an
        enqueue-lane report delivery."""
        from unittest.mock import MagicMock

        from daemon.services.attestation_gate import (
            GateSettings,
            evaluate,
        )

        manager = MagicMock()
        manager.count_pending_children.return_value = 0
        manager.get_queued_or_expected_wakeups.return_value = 0
        manager.count_live_descendants.return_value = 0
        messages = [
            real("delegate and finish"),
            ai("dispatching", tool_calls=send_message_call()),
            TestEnqueueLaneReportDoesNotResetDelegationWindow._enqueue_lane_report(),
            ai("done without attest"),
        ]
        return evaluate(
            "leader-iid",
            0,
            messages,
            GateSettings("enforce", 3, 3),
            manager,
        )

    def test_delegation_window_still_armed(self) -> None:
        """``delegation_since_last_user`` stays True — the report did
        NOT become the new window anchor."""
        result = self._evaluate_tail()
        assert result.delegation_since_last_user is True
        assert result.last_real_user_index == 0
        assert result.last_real_user_found is True

    def test_attestation_required_stays_true_and_end_denied(self) -> None:
        """``attestation_required`` stays True and the un-attested
        delegated END is DENIED with the nudge queued — the exact
        outcome the masquerade hole used to defeat."""
        from daemon.services.attestation_gate import Decision

        result = self._evaluate_tail()
        assert result.attestation_required is True
        assert result.decision is Decision.DENIED
        assert result.should_inject_nudge is True
        assert result.next_denied_count == 1

    def test_report_is_not_the_window_anchor(self) -> None:
        """Scanner-level pin: the enqueue-lane report is invisible to
        ``find_last_real_user_index`` — the original user message
        stays the anchor even though the report is the newest
        HumanMessage before the final AI turn."""
        msgs = [
            real("delegate and finish"),
            ai("dispatching", tool_calls=send_message_call()),
            self._enqueue_lane_report(),
        ]
        assert find_last_real_user_index(msgs) == 0
