"""Unit tests for the attestation scanner (Phase 2, task 2.1).

Covers ``daemon/services/attestation_scanner.py`` against the FR-2
acceptance criteria and the D10 edge cases:

* AC-2.1 — attested within window
* AC-2.2 — attested outside window
* AC-2.3 — text-only claim does not count
* AC-2.4 — non-attestation tool calls do not count
* AC-2.5 — window bounds (1000-message state, N=3 → ≤3 AIMessages)
* D10(a) — ANY-in-last-N-AIMessages semantics
* D10(b) — compaction summary doc awareness (summary_seen)
* injected-marker exclusion (language_check_reminder HumanMessages)
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from daemon.services.attestation_scanner import (
    DEFAULT_ATTESTATION_TOOL_NAME,
    attestation_seen_outside_window,
    is_compaction_summary_doc,
    scan_for_attestation,
    scan_for_attestation_detailed,
)


def ai(content="working", tool_calls=None, **kwargs):
    return AIMessage(content=content, tool_calls=tool_calls or [], **kwargs)


def attest_call(name=DEFAULT_ATTESTATION_TOOL_NAME, tc_id="tc-1"):
    return [{"name": name, "args": {}, "id": tc_id}]


class TestAC21AttestedWithinWindow:
    """AC-2.1 — an attesting AIMessage inside the last N=3 AIMessages counts."""

    def test_attest_on_newest_message(self):
        messages = [
            HumanMessage(content="go"),
            ai("doing", tool_calls=attest_call()),
        ]
        attested, diag = scan_for_attestation(messages, 3)
        assert attested is True
        assert any(d["attestation_present"] for d in diag)

    def test_attest_two_positions_back(self):
        # The natural attest → ToolMessage → final prose flow (D10(a))
        messages = [
            HumanMessage(content="go"),
            ai("attesting", tool_calls=attest_call()),
            ToolMessage(content="attested", tool_call_id="tc-1"),
            ai("All done."),
        ]
        attested, _ = scan_for_attestation(messages, 3)
        assert attested is True

    def test_attest_at_window_edge(self):
        # Exactly the 3rd AIMessage back — still inside the window.
        messages = [
            ai("old but attesting", tool_calls=attest_call()),
            HumanMessage(content="mid"),
            ToolMessage(content="x", tool_call_id="tc-1"),
            ai("middle"),
            HumanMessage(content="later"),
            ai("newest"),
        ]
        attested, _ = scan_for_attestation(messages, 3)
        assert attested is True


class TestAC22AttestedOutsideWindow:
    """AC-2.2 — an attestation older than the window does not count."""

    def test_attest_beyond_window(self):
        messages = [ai("attesting", tool_calls=attest_call())]
        # N+1 other messages after the attesting one
        for i in range(4):
            messages.append(HumanMessage(content=f"filler {i}"))
            messages.append(ai(f"chat {i}"))
        attested, _ = scan_for_attestation(messages, 3)
        assert attested is False

    def test_attested_outside_flagged_by_o3_helper(self):
        messages = [ai("attesting", tool_calls=attest_call())]
        for i in range(4):
            messages.append(HumanMessage(content=f"filler {i}"))
            messages.append(ai(f"chat {i}"))
        assert attestation_seen_outside_window(messages, 3) is True
        # ...while the attested decision stays False (bounded scan).
        attested, _ = scan_for_attestation(messages, 3)
        assert attested is False


class TestAC23TextOnlyClaim:
    """AC-2.3 — mentioning the tool in TEXT never counts."""

    def test_text_mention_does_not_count(self):
        messages = [
            HumanMessage(content="status?"),
            ai("I am done. Calling attest_completion now."),  # empty tool_calls
        ]
        attested, _ = scan_for_attestation(messages, 3)
        assert attested is False

    def test_text_mention_with_empty_tool_calls_list(self):
        messages = [ai("attest_completion() incoming", tool_calls=[])]
        assert scan_for_attestation(messages, 3)[0] is False


class TestAC24NonAttestationToolCalls:
    """AC-2.4 — other tool calls never satisfy the gate."""

    def test_other_tools_only(self):
        messages = [
            HumanMessage(content="check"),
            ai(
                "checking",
                tool_calls=[{"name": "subtree_status", "args": {}, "id": "a"}],
            ),
            ToolMessage(content="ok", tool_call_id="a"),
            ai(
                "spawning",
                tool_calls=[{"name": "spawn_instance", "args": {}, "id": "b"}],
            ),
        ]
        attested, diag = scan_for_attestation(messages, 3)
        assert attested is False
        # diagnostics still record what WAS seen
        flat_names = [n for d in diag for n in d["tool_call_names"]]
        assert "subtree_status" in flat_names
        assert "spawn_instance" in flat_names

    def test_attest_among_other_tools(self):
        messages = [
            ai(
                "batch",
                tool_calls=[
                    {"name": "get_instance_info", "args": {}, "id": "a"},
                    {"name": DEFAULT_ATTESTATION_TOOL_NAME, "args": {}, "id": "b"},
                ],
            ),
        ]
        attested, diag = scan_for_attestation(messages, 3)
        assert attested is True
        assert diag[0]["tool_call_names"] == ["get_instance_info", DEFAULT_ATTESTATION_TOOL_NAME]


class TestAC25WindowBounds:
    """AC-2.5 — bounded scan: 1000-message state, N=3 → ≤3 AIMessages."""

    def _big_state(self, n=1000):
        messages = []
        for i in range(n):
            if i % 2 == 0:
                messages.append(HumanMessage(content=f"m{i}"))
            else:
                messages.append(ai(f"a{i}"))
        return messages

    def test_only_window_aimessages_inspected(self):
        messages = self._big_state(1000)
        attested, diag = scan_for_attestation(messages, 3)
        assert len(diag) == 3  # exactly 3 AIMessages inspected, not 500
        detailed = scan_for_attestation_detailed(messages, 3)
        assert detailed.messages_scanned == 3
        assert attested is False

    def test_window_truncated_flag_false_when_tail_is_full(self):
        detailed = scan_for_attestation_detailed(self._big_state(1000), 3)
        assert detailed.window_truncated is False

    def test_window_truncated_flag_true_when_fewer_aimessages_exist(self):
        messages = [HumanMessage(content="only human")]
        detailed = scan_for_attestation_detailed(messages, 3)
        assert detailed.messages_scanned == 0
        assert detailed.window_truncated is True

    def test_attestation_deep_in_big_state_not_seen(self):
        # Attestation at the very HEAD of a 1000-message state — must
        # not leak into the attested decision (bounded walk stops).
        messages = [ai("attesting", tool_calls=attest_call())]
        messages.extend(self._big_state(999))
        attested, diag = scan_for_attestation(messages, 3)
        assert attested is False
        assert len(diag) == 3


class TestD10EdgeCases:
    """D10 — visibility edge cases (summaries, injected markers)."""

    def test_compaction_summary_doc_not_attestation_but_seen(self):
        summary = SystemMessage(
            content="[compaction] summary of earlier context",
            id="compaction-global-iid-0001",
        )
        messages = [
            HumanMessage(content="go"),
            summary,
            ai("post-compaction turn"),
        ]
        assert is_compaction_summary_doc(summary) is True
        detailed = scan_for_attestation_detailed(messages, 3)
        assert detailed.summary_seen is True
        assert detailed.attested is False
        # the summary doc is NOT counted as an inspected AIMessage
        assert detailed.messages_scanned == 1

    def test_regular_system_message_not_flagged_as_summary(self):
        messages = [
            SystemMessage(content="plain system message", id="sys-1"),
            ai("reply"),
        ]
        detailed = scan_for_attestation_detailed(messages, 3)
        assert detailed.summary_seen is False

    def test_language_check_reminder_human_message_excluded(self):
        # Injected reminder HumanMessages (language_check precedent)
        # carry additional_kwargs markers — they are HumanMessages, so
        # the AIMessage-only scan is immune by construction (D10(c)).
        reminder = HumanMessage(
            content="Please respond again in English.",
            additional_kwargs={"language_check_reminder": True},
        )
        attestation_nudge = HumanMessage(
            content="The work is not yet finished — check current progress and continue.",
            additional_kwargs={"attestation_nudge": True},
        )
        messages = [
            HumanMessage(content="go"),
            ai("working"),
            reminder,
            attestation_nudge,
            ai("still working"),
        ]
        attested, diag = scan_for_attestation(messages, 3)
        assert attested is False
        # only AIMessages appear in diagnostics
        assert all("tool_call_names" in d for d in diag)
        assert len(diag) == 2

    def test_attestation_nudge_marker_does_not_hijack_scan(self):
        # Even if a marker-keyed message existed, the scanner keys ONLY
        # on AIMessage.tool_calls[i].name — additional_kwargs never match.
        fake = AIMessage(
            content="nudge",
            additional_kwargs={"attestation_nudge": True},
        )
        assert scan_for_attestation([fake], 3)[0] is False


class TestScanMechanics:
    """Plan-verbatim signature + diagnostic shape details."""

    def test_plan_verbatim_signature_default_tool_name(self):
        messages = [ai("x", tool_calls=attest_call())]
        attested, diag = scan_for_attestation(messages, 3)
        assert attested is True
        assert diag == [
            {
                "index": 0,
                "tool_call_names": [DEFAULT_ATTESTATION_TOOL_NAME],
                "attestation_present": True,
            }
        ]

    def test_custom_tool_name(self):
        messages = [ai("x", tool_calls=attest_call(name="custom_attest"))]
        assert scan_for_attestation(messages, 3, tool_name="custom_attest")[0] is True
        assert scan_for_attestation(messages, 3, tool_name="attest_completion")[0] is False

    def test_diagnostics_ordered_oldest_first(self):
        messages = [
            ai("first"),
            HumanMessage(content="mid"),
            ai("second", tool_calls=attest_call()),
            ai("third"),
        ]
        _, diag = scan_for_attestation(messages, 3)
        assert [d["index"] for d in diag] == [0, 2, 3]

    def test_zero_window_clamped_to_one(self):
        messages = [ai("x", tool_calls=attest_call()), ai("y")]
        attested, diag = scan_for_attestation(messages, 0)
        assert attested is False  # newest AI message has no attest call
        assert len(diag) == 1

    def test_empty_message_list(self):
        attested, diag = scan_for_attestation([], 3)
        assert attested is False
        assert diag == []
        detailed = scan_for_attestation_detailed([], 3)
        assert detailed.messages_scanned == 0
        assert detailed.window_truncated is True
        assert detailed.summary_seen is False

    def test_o3_helper_ignores_window_messages_and_summaries(self):
        summary = SystemMessage(content="s", id="compaction-global-iid-0002")
        messages = [
            ai("stale attesting", tool_calls=attest_call()),
            summary,  # crossed boundary — ignored, not counted
            ai("w1"),
            ai("w2"),
            ai("w3"),
        ]
        assert attestation_seen_outside_window(messages, 3) is True
        # no attestation anywhere → False
        assert attestation_seen_outside_window([ai("plain")], 3) is False
        # attestation only INSIDE the window → False (not "outside")
        inside = [ai("a", tool_calls=attest_call()), ai("b"), ai("c")]
        assert attestation_seen_outside_window(inside, 3) is False
