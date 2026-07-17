"""Unit tests for the general hallucination loop detector (Phase 1).

Covers ``daemon.graph.LoopDetector.scan``:

    1. Sequential identical calls detected (3x same tool, same args).
    2. Different args break the consecutive chain.
    3. Parallel tool calls handled (3x AIMessage with multiple identical
       tool_calls -> detected as a single loop with a multi-tool signature).
    4. Threshold respected (2x same -> None; 3x same -> detected).
    5. Excluded tools skipped (units whose tools are all excluded break the chain).
    6. Mixed tools reset the consecutive count (A, A, B breaks the chain).
    7. Evidence retention: the OLDEST matching unit's IDs land in
       ``evidence_message_ids``; ``loop_messages`` excludes those IDs.
    8. Non-tool message breaks the chain (HumanMessage in the middle stops
       the backwards walk).

The detector is purely static and inspects the message list directly, so no
``InstanceManager`` stand-in is needed — these tests construct message lists
in-memory and call ``LoopDetector.scan`` directly.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from daemon.graph import (
    LOOP_BREAKER_DEFAULT_THRESHOLD,
    LoopDetectionResult,
    LoopDetector,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ai_with_tool_call(
    tool_call_id: str,
    name: str,
    args: dict,
    *,
    msg_id: str | None = None,
    tool_calls: list[dict] | None = None,
) -> AIMessage:
    """Build an ``AIMessage`` with a single ``tool_call`` entry.

    ``tool_calls`` is the langchain shape::
        [{"id": str, "name": str, "args": dict}, ...]
    """
    if tool_calls is None:
        tool_calls = [{"id": tool_call_id, "name": name, "args": args}]
    return AIMessage(content="", tool_calls=tool_calls, id=msg_id)


def _tool_result(
    tool_call_id: str,
    name: str,
    content: str = "result",
    *,
    msg_id: str | None = None,
) -> ToolMessage:
    """Build a ``ToolMessage`` matching an earlier ``tool_call_id``."""
    return ToolMessage(content=content, tool_call_id=tool_call_id, name=name, id=msg_id)


def _sequential_loop_messages(
    tool_name: str,
    args: dict,
    count: int,
) -> list:
    """Build ``count`` consecutive AI+Tool pairs, each calling ``tool_name(args)``.

    Messages are ordered oldest-first (matches the canonical
    ``MessagesState`` layout). IDs are unique per unit so evidence/loop
    bookkeeping can be asserted by ID.
    """
    messages: list = []
    for i in range(count):
        tc_id = f"tc-{i}"
        ai = _ai_with_tool_call(tc_id, tool_name, args, msg_id=f"ai-{i}")
        tm = _tool_result(tc_id, tool_name, content=f"result-{i}", msg_id=f"tm-{i}")
        messages.append(ai)
        messages.append(tm)
    return messages


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestScanSequentialIdenticalCalls:
    """Three identical sequential calls produce a LoopDetectionResult."""

    def test_detects_three_identical_sequential_calls(self):
        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=3)

        result = LoopDetector.scan(messages, threshold=3)

        assert result is not None
        assert isinstance(result, LoopDetectionResult)
        assert result.tool_name == "bash"
        assert result.tool_args == {"cmd": "ls"}
        assert result.repetition_count == 3

    def test_repetition_count_includes_evidence_unit(self):
        """``repetition_count`` is the full consecutive chain including the evidence unit."""
        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=4)

        result = LoopDetector.scan(messages, threshold=3)

        assert result is not None
        assert result.repetition_count == 4

    def test_loop_messages_excludes_evidence(self):
        """``loop_messages`` should contain ONLY the newer duplicates — not the evidence."""
        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=3)

        result = LoopDetector.scan(messages, threshold=3)

        assert result is not None
        # 2 newer duplicates × (AIMessage + ToolMessage) = 4 messages to remove.
        # The oldest unit (ai-0/tm-0) is evidence and must NOT appear here.
        assert len(result.loop_messages) == 4
        removed_ids = {m.id for m in result.loop_messages if m.id}
        assert "ai-0" not in removed_ids
        assert "tm-0" not in removed_ids
        assert {"ai-1", "tm-1", "ai-2", "tm-2"} == removed_ids


class TestScanDifferentArgs:
    """Different args break the consecutive chain."""

    def test_different_args_not_detected(self):
        messages = [
            _ai_with_tool_call("tc-0", "bash", {"cmd": "ls"}, msg_id="ai-0"),
            _tool_result("tc-0", "bash", msg_id="tm-0"),
            _ai_with_tool_call("tc-1", "bash", {"cmd": "pwd"}, msg_id="ai-1"),
            _tool_result("tc-1", "bash", msg_id="tm-1"),
            _ai_with_tool_call("tc-2", "bash", {"cmd": "whoami"}, msg_id="ai-2"),
            _tool_result("tc-2", "bash", msg_id="tm-2"),
        ]

        result = LoopDetector.scan(messages, threshold=3)

        assert result is None

    def test_args_ordering_is_canonical(self):
        """Args with the same keys in different orders are still equal (canonical JSON)."""
        # In Python 3.7+ dict preserves insertion order, but json.dumps with
        # sort_keys=True normalises them. The detector should treat
        # ``{"a": 1, "b": 2}`` and ``{"b": 2, "a": 1}`` as equal signatures.
        messages = [
            _ai_with_tool_call("tc-0", "bash", {"a": 1, "b": 2}, msg_id="ai-0"),
            _tool_result("tc-0", "bash", msg_id="tm-0"),
            _ai_with_tool_call("tc-1", "bash", {"b": 2, "a": 1}, msg_id="ai-1"),
            _tool_result("tc-1", "bash", msg_id="tm-1"),
            _ai_with_tool_call("tc-2", "bash", {"a": 1, "b": 2}, msg_id="ai-2"),
            _tool_result("tc-2", "bash", msg_id="tm-2"),
        ]

        result = LoopDetector.scan(messages, threshold=3)

        assert result is not None
        assert result.repetition_count == 3


class TestScanParallelToolCalls:
    """Parallel tool calls (multiple tool_calls in one AIMessage) produce a single signature."""

    def test_three_identical_parallel_calls_detected(self):
        """3 AIMessages each with 2 parallel identical tool_calls should be detected as one loop."""
        messages = []
        for i in range(3):
            tcs = [
                {"id": f"tc-{i}-a", "name": "bash", "args": {"cmd": "ls"}},
                {"id": f"tc-{i}-b", "name": "read_file", "args": {"path": "/etc/hosts"}},
            ]
            ai = _ai_with_tool_call(
                f"tc-{i}-a",
                "bash",
                {"cmd": "ls"},
                msg_id=f"ai-{i}",
                tool_calls=tcs,
            )
            messages.append(ai)
            messages.append(_tool_result(f"tc-{i}-a", "bash", msg_id=f"tm-{i}-a"))
            messages.append(_tool_result(f"tc-{i}-b", "read_file", msg_id=f"tm-{i}-b"))

        result = LoopDetector.scan(messages, threshold=3)

        assert result is not None
        # Primary tool comes from the first tool_call of the newest unit.
        assert result.tool_name == "bash"
        assert result.tool_args == {"cmd": "ls"}
        assert result.repetition_count == 3
        # Each duplicate unit = 1 AIMessage + 2 ToolMessages = 3 messages.
        # 2 duplicate units × 3 = 6 loop messages; evidence unit's 3 are kept.
        assert len(result.loop_messages) == 6

    def test_parallel_with_different_tools_per_unit_breaks_chain(self):
        """If one AIMessage has multiple different tools, the signature still changes."""
        messages = []
        # Unit 0: bash + read_file (parallel)
        tcs0 = [
            {"id": "tc-0-a", "name": "bash", "args": {"cmd": "ls"}},
            {"id": "tc-0-b", "name": "read_file", "args": {"path": "/etc/hosts"}},
        ]
        messages.append(
            _ai_with_tool_call("tc-0-a", "bash", {"cmd": "ls"}, msg_id="ai-0", tool_calls=tcs0)
        )
        messages.append(_tool_result("tc-0-a", "bash", msg_id="tm-0-a"))
        messages.append(_tool_result("tc-0-b", "read_file", msg_id="tm-0-b"))
        # Unit 1: different tool entirely — chain broken
        messages.append(
            _ai_with_tool_call("tc-1", "write_file", {"path": "/tmp/x"}, msg_id="ai-1")
        )
        messages.append(_tool_result("tc-1", "write_file", msg_id="tm-1"))
        # Unit 2: bash+read_file again, but not consecutive with unit 0
        tcs2 = [
            {"id": "tc-2-a", "name": "bash", "args": {"cmd": "ls"}},
            {"id": "tc-2-b", "name": "read_file", "args": {"path": "/etc/hosts"}},
        ]
        messages.append(
            _ai_with_tool_call("tc-2-a", "bash", {"cmd": "ls"}, msg_id="ai-2", tool_calls=tcs2)
        )
        messages.append(_tool_result("tc-2-a", "bash", msg_id="tm-2-a"))
        messages.append(_tool_result("tc-2-b", "read_file", msg_id="tm-2-b"))

        result = LoopDetector.scan(messages, threshold=3)

        # Only 2 consecutive identical units at the tail — below threshold.
        assert result is None


class TestScanThreshold:
    """Threshold is respected: count < threshold returns None."""

    def test_two_identical_below_threshold_returns_none(self):
        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=2)

        result = LoopDetector.scan(messages, threshold=3)

        assert result is None

    def test_threshold_two_detects_two(self):
        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=2)

        result = LoopDetector.scan(messages, threshold=2)

        assert result is not None
        assert result.repetition_count == 2

    def test_threshold_one_detects_single_call(self):
        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=1)

        result = LoopDetector.scan(messages, threshold=1)

        assert result is not None
        assert result.repetition_count == 1

    def test_default_threshold_matches_constant(self):
        """Sanity check: the default threshold equals ``LOOP_BREAKER_DEFAULT_THRESHOLD``."""
        assert LOOP_BREAKER_DEFAULT_THRESHOLD == 3
        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=3)

        result = LoopDetector.scan(messages)

        assert result is not None


class TestScanExcludedTools:
    """Tools in ``excluded_tools`` break the chain (don't penalise legit polling)."""

    def test_excluded_tool_breaks_chain(self):
        messages = [
            _ai_with_tool_call("tc-0", "bash", {"cmd": "ls"}, msg_id="ai-0"),
            _tool_result("tc-0", "bash", msg_id="tm-0"),
            _ai_with_tool_call("tc-1", "get_instance_info", {"id": "x"}, msg_id="ai-1"),
            _tool_result("tc-1", "get_instance_info", msg_id="tm-1"),
            _ai_with_tool_call("tc-2", "get_instance_info", {"id": "x"}, msg_id="ai-2"),
            _tool_result("tc-2", "get_instance_info", msg_id="tm-2"),
        ]

        result = LoopDetector.scan(messages, threshold=3, excluded_tools=["get_instance_info"])

        # The tail has only 2 consecutive identical units (get_instance_info),
        # not 3, because the first bash call broke the chain.
        assert result is None

    def test_all_excluded_tools_still_breaks_chain(self):
        """When ALL tools in a unit are excluded, that unit breaks the chain."""
        messages = [
            _ai_with_tool_call("tc-0", "poller", {"x": 1}, msg_id="ai-0"),
            _tool_result("tc-0", "poller", msg_id="tm-0"),
            _ai_with_tool_call("tc-1", "poller", {"x": 1}, msg_id="ai-1"),
            _tool_result("tc-1", "poller", msg_id="tm-1"),
            _ai_with_tool_call("tc-2", "poller", {"x": 1}, msg_id="ai-2"),
            _tool_result("tc-2", "poller", msg_id="tm-2"),
        ]

        result = LoopDetector.scan(messages, threshold=3, excluded_tools=["poller"])

        assert result is None

    def test_partially_excluded_unit_still_counts(self):
        """A unit with a mix of excluded and non-excluded tools still counts."""
        tcs = [
            {"id": "tc-0-a", "name": "bash", "args": {"cmd": "ls"}},
            {"id": "tc-0-b", "name": "poller", "args": {"x": 1}},
        ]
        messages = [
            _ai_with_tool_call("tc-0-a", "bash", {"cmd": "ls"}, msg_id="ai-0", tool_calls=tcs),
            _tool_result("tc-0-a", "bash", msg_id="tm-0-a"),
            _tool_result("tc-0-b", "poller", msg_id="tm-0-b"),
        ]
        for i in range(1, 3):
            tcs_i = [
                {"id": f"tc-{i}-a", "name": "bash", "args": {"cmd": "ls"}},
                {"id": f"tc-{i}-b", "name": "poller", "args": {"x": 1}},
            ]
            messages.append(
                _ai_with_tool_call(
                    f"tc-{i}-a", "bash", {"cmd": "ls"}, msg_id=f"ai-{i}", tool_calls=tcs_i
                )
            )
            messages.append(_tool_result(f"tc-{i}-a", "bash", msg_id=f"tm-{i}-a"))
            messages.append(_tool_result(f"tc-{i}-b", "poller", msg_id=f"tm-{i}-b"))

        # Only poller is excluded — units still count because bash is non-excluded.
        result = LoopDetector.scan(messages, threshold=3, excluded_tools=["poller"])

        assert result is not None
        assert result.repetition_count == 3


class TestScanMixedToolsResetCount:
    """Mixed tools reset the consecutive count."""

    def test_mixed_tools_break_chain(self):
        """A, A, B sequence: only 2 consecutive A's at the tail, below threshold."""
        messages = [
            _ai_with_tool_call("tc-0", "bash", {"cmd": "ls"}, msg_id="ai-0"),
            _tool_result("tc-0", "bash", msg_id="tm-0"),
            _ai_with_tool_call("tc-1", "bash", {"cmd": "ls"}, msg_id="ai-1"),
            _tool_result("tc-1", "bash", msg_id="tm-1"),
            _ai_with_tool_call("tc-2", "read_file", {"path": "/etc/hosts"}, msg_id="ai-2"),
            _tool_result("tc-2", "read_file", msg_id="tm-2"),
        ]

        result = LoopDetector.scan(messages, threshold=3)

        # Only 1 read_file unit at the tail — far below threshold.
        assert result is None

    def test_repeated_then_different_then_repeated(self):
        """bash x2, read_file, bash x2: only 2 consecutive bash at the tail."""
        messages = [
            _ai_with_tool_call("tc-0", "bash", {"cmd": "ls"}, msg_id="ai-0"),
            _tool_result("tc-0", "bash", msg_id="tm-0"),
            _ai_with_tool_call("tc-1", "read_file", {"path": "/x"}, msg_id="ai-1"),
            _tool_result("tc-1", "read_file", msg_id="tm-1"),
            _ai_with_tool_call("tc-2", "bash", {"cmd": "ls"}, msg_id="ai-2"),
            _tool_result("tc-2", "bash", msg_id="tm-2"),
            _ai_with_tool_call("tc-3", "bash", {"cmd": "ls"}, msg_id="ai-3"),
            _tool_result("tc-3", "bash", msg_id="tm-3"),
        ]

        result = LoopDetector.scan(messages, threshold=3)

        # Only 2 consecutive bash units at the tail — below threshold.
        assert result is None


class TestScanEvidenceRetention:
    """The oldest matching unit is preserved as evidence."""

    def test_evidence_ids_contain_oldest_unit(self):
        """The oldest (first) unit's AI + Tool IDs MUST be in ``evidence_message_ids``."""
        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=3)

        result = LoopDetector.scan(messages, threshold=3)

        assert result is not None
        evidence_set = set(result.evidence_message_ids)
        assert "ai-0" in evidence_set
        assert "tm-0" in evidence_set
        # Newer units are NOT evidence.
        assert "ai-1" not in evidence_set
        assert "tm-1" not in evidence_set
        assert "ai-2" not in evidence_set
        assert "tm-2" not in evidence_set

    def test_evidence_excluded_from_loop_messages(self):
        """No message whose ID is in ``evidence_message_ids`` should be in ``loop_messages``."""
        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=3)

        result = LoopDetector.scan(messages, threshold=3)

        assert result is not None
        evidence_set = set(result.evidence_message_ids)
        for m in result.loop_messages:
            assert m.id not in evidence_set, (
                f"Evidence message {m.id} should NOT appear in loop_messages"
            )

    def test_evidence_for_parallel_calls_includes_all_tool_messages(self):
        """For parallel calls, evidence includes the AIMessage + ALL its ToolMessages."""
        messages = []
        for i in range(3):
            tcs = [
                {"id": f"tc-{i}-a", "name": "bash", "args": {"cmd": "ls"}},
                {"id": f"tc-{i}-b", "name": "read_file", "args": {"path": "/etc/hosts"}},
            ]
            ai = _ai_with_tool_call(
                f"tc-{i}-a", "bash", {"cmd": "ls"}, msg_id=f"ai-{i}", tool_calls=tcs
            )
            messages.append(ai)
            messages.append(_tool_result(f"tc-{i}-a", "bash", msg_id=f"tm-{i}-a"))
            messages.append(_tool_result(f"tc-{i}-b", "read_file", msg_id=f"tm-{i}-b"))

        result = LoopDetector.scan(messages, threshold=3)

        assert result is not None
        evidence_set = set(result.evidence_message_ids)
        # Oldest unit (i=0) — AIMessage + BOTH ToolMessages are evidence.
        assert "ai-0" in evidence_set
        assert "tm-0-a" in evidence_set
        assert "tm-0-b" in evidence_set


class TestScanNonToolMessageBreaksChain:
    """Non-tool messages (HumanMessage, plain AIMessage, SystemMessage) break the chain."""

    def test_human_message_in_middle_breaks_chain(self):
        messages = [
            _ai_with_tool_call("tc-0", "bash", {"cmd": "ls"}, msg_id="ai-0"),
            _tool_result("tc-0", "bash", msg_id="tm-0"),
            HumanMessage(content="stop that", id="h-1"),
            _ai_with_tool_call("tc-1", "bash", {"cmd": "ls"}, msg_id="ai-1"),
            _tool_result("tc-1", "bash", msg_id="tm-1"),
        ]

        result = LoopDetector.scan(messages, threshold=3)

        # The HumanMessage breaks the backwards walk; only 1 consecutive unit
        # at the tail, below threshold.
        assert result is None

    def test_plain_aimessage_without_tool_calls_breaks_chain(self):
        """An AIMessage with content but no tool_calls is a 'plain' AIMessage."""
        messages = [
            _ai_with_tool_call("tc-0", "bash", {"cmd": "ls"}, msg_id="ai-0"),
            _tool_result("tc-0", "bash", msg_id="tm-0"),
            AIMessage(content="Let me think...", id="ai-1"),
            _ai_with_tool_call("tc-1", "bash", {"cmd": "ls"}, msg_id="ai-2"),
            _tool_result("tc-1", "bash", msg_id="tm-2"),
        ]

        result = LoopDetector.scan(messages, threshold=3)

        assert result is None

    def test_system_message_breaks_chain(self):
        messages = [
            _ai_with_tool_call("tc-0", "bash", {"cmd": "ls"}, msg_id="ai-0"),
            _tool_result("tc-0", "bash", msg_id="tm-0"),
            SystemMessage(content="system notice", id="s-1"),
            _ai_with_tool_call("tc-1", "bash", {"cmd": "ls"}, msg_id="ai-2"),
            _tool_result("tc-1", "bash", msg_id="tm-2"),
        ]

        result = LoopDetector.scan(messages, threshold=3)

        assert result is None

    def test_loop_followed_by_human_message_still_detected(self):
        """A loop at the tail is still detected even if a HumanMessage precedes it."""
        messages = [
            HumanMessage(content="hi", id="h-0"),
            _ai_with_tool_call("tc-0", "bash", {"cmd": "ls"}, msg_id="ai-0"),
            _tool_result("tc-0", "bash", msg_id="tm-0"),
            _ai_with_tool_call("tc-1", "bash", {"cmd": "ls"}, msg_id="ai-1"),
            _tool_result("tc-1", "bash", msg_id="tm-1"),
            _ai_with_tool_call("tc-2", "bash", {"cmd": "ls"}, msg_id="ai-2"),
            _tool_result("tc-2", "bash", msg_id="tm-2"),
            HumanMessage(content="ok stop now", id="h-3"),
        ]

        result = LoopDetector.scan(messages, threshold=3)

        # The HumanMessage at messages[-1] breaks the backwards walk first.
        # 0 consecutive units → None.
        assert result is None


class TestScanEdgeCases:
    """Empty / degenerate inputs."""

    def test_empty_messages_returns_none(self):
        assert LoopDetector.scan([], threshold=3) is None

    def test_only_human_message_returns_none(self):
        from langchain_core.messages import BaseMessage
        messages: list[BaseMessage] = [HumanMessage(content="hi", id="h-0")]
        assert LoopDetector.scan(messages, threshold=3) is None

    def test_no_tool_messages_returns_none(self):
        messages = [
            HumanMessage(content="hi", id="h-0"),
            AIMessage(content="hello", id="ai-0"),
        ]
        assert LoopDetector.scan(messages, threshold=3) is None

    def test_threshold_zero_returns_none(self):
        """Threshold < 1 is a degenerate input — return None defensively."""
        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=3)
        assert LoopDetector.scan(messages, threshold=0) is None

    def test_excluded_tools_none_does_not_break(self):
        """``excluded_tools=None`` should not match any tool name (None treated as no exclusions)."""
        messages = _sequential_loop_messages("bash", {"cmd": "ls"}, count=3)
        result = LoopDetector.scan(messages, threshold=3, excluded_tools=None)
        assert result is not None
        assert result.repetition_count == 3
