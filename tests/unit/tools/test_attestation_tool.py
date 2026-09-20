"""Tool return-shape and idempotency tests for ``attest_completion``.

The LCA feature's completion gate scans the leader's most recent
``N`` AIMessages for an ``attest_completion`` tool_call. The contract
implemented by ``daemon/tools/attestation.py`` is:

* The tool is **no-arg** — the args schema is empty.
* The tool is **idempotent** — any call in the lookback window counts.
* The tool returns a **string teacher text** for the NEXT AI
  message — the clean-call shape
  (``ATTEST_CLEAN_RESULT_TEXT``) when the calling AIMessage had
  empty content, the bundled-shape text
  (``ATTEST_BUNDLED_RESULT_TEXT``) when it carried text (the
  c5d9a38a shape). The teacher text tells the leader what
  shape the next message must take.
* The tool **does not mutate state** — the attestation is recorded by
  virtue of the tool call existing in the message stream; the return
  value is the teacher text the LLM reads via the ToolMessage.

These tests pin the contract at the StructuredTool level so a
maintainer refactoring the body cannot silently break the
return-shape invariant (the Phase 2 scanner reads the tool_call name,
not the return value — but the return value is what the leader sees
in its ToolMessage and is the surface area the agent experiences).

ATTEST-FIRST PURE-TOOLCALL-TURN CONTRACT (2026-09-19, c5d9a38a
remediation): the tool body picks the teacher text by inspecting
the per-thread runtime state set by the tools-node caller in
``daemon/services/long_tool_nudge.py:wrapped_tools_node``. Tests
that need to exercise the bundled-shape path set the runtime
state directly via ``daemon.tools.attestation.set_attest_caller_
content`` and clean it up in the fixture's teardown.
"""
from __future__ import annotations

import pytest


# ── Tool return-shape contract ───────────────────────────────────────────────


class TestAttestCompletionReturnShape:
    """The tool returns the teacher text (string) — the deterministic
    teacher surface the LLM reads via the ToolMessage. Per the
    2026-09-19 attest-first contract, the return shape depends on
    whether the calling AIMessage had empty content (clean-call
    shape) or carried text (bundled-shape, c5d9a38a). The
    scanner reads the tool_call name, not the return value —
    but the return value is the surface area the agent
    experiences.
    """

    @pytest.fixture
    def _clear_caller_state(self):
        """Clear the per-thread caller-AIMessage state around each test.

        Some tests in this class set the state directly to exercise
        the bundled-shape path. The fixture guarantees clean state
        on entry AND on exit so test order independence holds.
        """
        from daemon.tools.attestation import (
            reset_attest_caller_content_for_tests,
        )

        reset_attest_caller_content_for_tests()
        yield
        reset_attest_caller_content_for_tests()

    @pytest.fixture
    def tool(self):
        from daemon.tools.attestation import attest_completion

        return attest_completion

    def test_return_is_string(self, tool, _clear_caller_state) -> None:
        """The tool return MUST be a string — the teacher text the LLM
        reads verbatim via the ToolMessage. NOT a dict (the OLD
        ``{"attested": True, "timestamp": "..."}`` shape retired
        2026-09-19 with the attest-first contract; the teacher
        text replaces it as the surface area the leader
        experiences)."""
        result = tool.invoke({})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_clean_caller_returns_clean_teacher_text(
        self, tool, _clear_caller_state
    ) -> None:
        """When the calling AIMessage had empty content (the canonical
        clean-call shape), the tool returns the
        ``ATTEST_CLEAN_RESULT_TEXT`` teacher text — the one that
        tells the leader to deliver the full detailed final report
        as its final message."""
        from daemon.tools.attestation import (
            ATTEST_CLEAN_RESULT_TEXT,
        )

        # Per-thread state is empty by default (clean-caller case).
        result = tool.invoke({})
        assert result == ATTEST_CLEAN_RESULT_TEXT
        # The clean-call text leads with the canonical instruction
        # and contains the explicit "deliver your full detailed
        # final report as your final message" phrasing.
        assert "Attestation recorded" in result
        assert "deliver your full detailed final report" in result
        assert "standalone message with no tool calls" in result

    def test_bundled_caller_returns_bundled_teacher_text(
        self, tool, _clear_caller_state
    ) -> None:
        """When the calling AIMessage carried non-empty content (the
        c5d9a38a bundled shape — report + attest in ONE message),
        the tool returns the ``ATTEST_BUNDLED_RESULT_TEXT``
        teacher text — the one that REJECTS the bundle and asks
        the leader to re-issue the report as its own standalone
        message. The runtime hook in the tools-node caller sets
        the per-thread state with the AIMessage content BEFORE
        the tool body runs."""
        from langchain_core.messages import AIMessage

        from daemon.tools.attestation import (
            ATTEST_BUNDLED_RESULT_TEXT,
            set_attest_caller_content,
        )

        # Simulate the tools-node caller setting the runtime state
        # with a bundled AIMessage (text + tool_call in ONE message).
        bundled_ai = AIMessage(
            content="Here is the full report: ... attesting now.",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "t1"}
            ],
        )
        set_attest_caller_content(bundled_ai)
        result = tool.invoke({})
        assert result == ATTEST_BUNDLED_RESULT_TEXT
        # The bundled-call text leads with the rejection + the
        # explicit re-issue instruction.
        assert "Attestation recorded, but your tool-call message contained text" in result
        assert "Re-issue your full detailed final report" in result

    def test_no_arg_signature(self, tool, _clear_caller_state) -> None:
        """The tool takes NO arguments. ``args`` is empty and
        ``invoke({})`` succeeds with no required keys."""
        assert tool.args == {}
        # Should NOT accept any keyword — empty kwargs only.
        result = tool.invoke({})
        assert isinstance(result, str)


# ── Idempotency contract ────────────────────────────────────────────────────


class TestAttestCompletionIdempotency:
    """Per the contract: ``attest_completion`` is idempotent — any
    call in the lookback window counts as the attestation. The
    tool body produces the SAME teacher text on every call (the
    contract is "ANY call counts", not "only one call per turn
    counts")."""

    @pytest.fixture
    def _clear_caller_state(self):
        from daemon.tools.attestation import (
            reset_attest_caller_content_for_tests,
        )

        reset_attest_caller_content_for_tests()
        yield
        reset_attest_caller_content_for_tests()

    def test_repeated_calls_all_return_teacher_text(
        self, _clear_caller_state,
    ) -> None:
        """N successive invocations all return the SAME teacher
        text — no per-call cooldown, no exception, no first-call-wins
        guard."""
        from daemon.tools.attestation import (
            ATTEST_CLEAN_RESULT_TEXT,
            attest_completion,
        )

        results = [
            attest_completion.invoke({}) for _ in range(5)
        ]
        # All return the clean teacher text (default empty caller
        # state).
        assert all(r == ATTEST_CLEAN_RESULT_TEXT for r in results)
        assert len(results) == 5

    def test_repeated_calls_with_bundled_caller_consistent(
        self, _clear_caller_state,
    ) -> None:
        """N successive invocations with the bundled-caller state
        all return the bundled teacher text."""
        from langchain_core.messages import AIMessage

        from daemon.tools.attestation import (
            ATTEST_BUNDLED_RESULT_TEXT,
            attest_completion,
            set_attest_caller_content,
        )

        bundled_ai = AIMessage(
            content="Bundled text.",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "t1"}
            ],
        )
        set_attest_caller_content(bundled_ai)
        results = [
            attest_completion.invoke({}) for _ in range(3)
        ]
        assert all(r == ATTEST_BUNDLED_RESULT_TEXT for r in results)


# ── Per-thread runtime state contract ────────────────────────────────────────


class TestAttestCallerRuntimeState:
    """The runtime hook (the tools-node caller in
    ``daemon/services/long_tool_nudge.py:wrapped_tools_node``) sets
    the per-thread caller-AIMessage content via
    ``set_attest_caller_content`` BEFORE invoking the tool. The
    tool body reads it via ``_resolve_attest_caller_content`` to
    pick the clean-call vs bundled-call teacher text. These
    tests pin the per-thread state contract."""

    @pytest.fixture
    def _clear_caller_state(self):
        from daemon.tools.attestation import (
            reset_attest_caller_content_for_tests,
        )

        reset_attest_caller_content_for_tests()
        yield
        reset_attest_caller_content_for_tests()

    def test_set_then_clear_roundtrip(self, _clear_caller_state) -> None:
        """Set then clear round-trips — the per-thread state is
        deterministic across the set/clear pair. The reset helper
        is the test-only public API (production code never invokes
        it)."""
        from langchain_core.messages import AIMessage

        from daemon.tools.attestation import (
            _get_attest_caller_content,
            reset_attest_caller_content_for_tests,
            set_attest_caller_content,
        )

        # Empty default.
        assert _get_attest_caller_content() == ""
        # Set bundled content.
        set_attest_caller_content(
            AIMessage(
                content="Bundled.",
                tool_calls=[
                    {"name": "attest_completion", "args": {}, "id": "t1"}
                ],
            )
        )
        assert _get_attest_caller_content() == "Bundled."
        # Clear.
        reset_attest_caller_content_for_tests()
        assert _get_attest_caller_content() == ""

    def test_none_caller_records_empty(self, _clear_caller_state) -> None:
        """Passing ``None`` to ``set_attest_caller_content`` records
        empty content — the degenerate / unset-runtime fallback.
        The clean-call teacher text is the safe default."""
        from daemon.tools.attestation import (
            _get_attest_caller_content,
            set_attest_caller_content,
        )

        set_attest_caller_content(None)
        assert _get_attest_caller_content() == ""

    def test_flatten_list_of_blocks(self, _clear_caller_state) -> None:
        """LangChain text+reasoning blocks content is flattened to
        plain text — the per-thread state carries the flattened
        content, not the raw list-of-blocks. The marker scanner's
        ``_flatten_ai_content`` is the canonical helper; the
        attestation tool mirrors it inline (kept dependency-light
        on the hot set-state path)."""
        from langchain_core.messages import AIMessage

        from daemon.tools.attestation import (
            _get_attest_caller_content,
            set_attest_caller_content,
        )

        list_content_ai = AIMessage(
            content=[
                {"type": "text", "text": "First block."},
                {"type": "reasoning", "text": "reasoning here"},
                {"type": "text", "text": "Second block."},
            ],
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "t1"}
            ],
        )
        set_attest_caller_content(list_content_ai)
        flattened = _get_attest_caller_content()
        # All three blocks contribute (concatenated + space-separated).
        assert "First block." in flattened
        assert "reasoning here" in flattened
        assert "Second block." in flattened


# ── Tool-result-as-teacher surface ──────────────────────────────────────────


class TestAttestCompletionIsNoOp:
    """The attestation tool body has NO side effects — the
    attestation is recorded by virtue of the tool call existing
    in the message stream; the return value is purely the teacher
    text. These tests pin the no-side-effect contract on the
    factory signature (mirrors the other category factories for
    symmetry — the manager / instance_id / agent_id parameters
    are unused but kept for tooling consistency)."""

    def test_factory_unused_args_are_ignored(self) -> None:
        """``create_attestation_tools`` accepts ``manager``,
        ``current_instance_id``, and ``agent_id`` but IGNORES them
        — the tool body is a static no-op that reads the per-thread
        runtime state instead. The factory signature mirrors the
        other category factories for tooling symmetry."""
        from daemon.tools.attestation import create_attestation_tools

        # Pass garbage for all three args — the factory must not
        # validate, store, or otherwise touch them.
        tools = create_attestation_tools(
            manager=None,
            current_instance_id="unused",
            agent_id="unused",
        )
        assert len(tools) == 1
        # The tool is the decorator-bound ``attest_completion``
        # function (the runtime reads its caller-AIMessage
        # context from per-thread state).
        assert tools[0].name == "attest_completion"
