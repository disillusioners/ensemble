"""TOOL-SURFACE chain tests for ``attest_completion`` (merge gate).

Pins the three dynamic requirements from the 2026-09-19 LCA attest-
first pure-toolcall-turn contract that the existing two attestation
test files leave implicit:

R1 — ContextVar bundled-detection FIRES the correction through the
     REAL runtime hook (not by calling the setter directly): an
     AIMessage with text + an ``attest_completion`` tool_call routed
     through ``daemon.services.long_tool_nudge.wrapped_tools_node``
     (the canonical LangGraph ``tools`` node wrapper) MUST yield the
     ``ATTEST_BUNDLED_RESULT_TEXT`` teacher text on the resulting
     ToolMessage. This is the c5d9a38a regression shape — report
     + attest bundled in ONE AIMessage; the gate catches it at the
     runtime seam, not at a mocked direct setter call.

R2 — Clean path through the SAME chain: an empty-content AIMessage
     routed through wrapped_tools_node MUST yield the
     ``ATTEST_CLEAN_RESULT_TEXT`` teacher text on the resulting
     ToolMessage. The hook fires with the empty-content AIMessage,
     ``set_attest_caller_content`` records ``""``, the tool body
     resolves to the clean shape.

R3 — DEGRADATION safe default: when the runtime hook never fires
     (fresh ``ContextVar``, ``reset_attest_caller_content_for_tests``
     has been called, no caller-AIMessage is set), ``attest_completion``
     MUST return ``ATTEST_CLEAN_RESULT_TEXT`` — the safe default
     documented in ``daemon/tools/attestation.py`` lines 132-160
     ("empty content maps to the clean-call teacher text — the safe
     default if the runtime forgets to set it"). The reset helper is
     the test-only public API; production code never invokes it.

Defense-in-depth — STACK-INSPECTOR FALLBACK: the tool body falls
back to ``_fallback_extract_attest_caller_content`` (an
``inspect.stack`` walker) when the runtime-set per-thread
``ContextVar`` is empty. The fallback is reachable only when:
(a) the runtime hook never set the ContextVar AND
(b) the calling stack contains an AIMessage with an
    ``attest_completion`` tool_call in some frame's local vars
    (e.g. a frozen unit test that constructs the AIMessage and calls
    the tool without going through wrapped_tools_node).
The fallback is unreachable in the canonical chain path (the hook
fires first) — pinning it here documents the contract.

These tests run under the repo-standard real-langgraph evict/restore
pattern (the suite's root conftest installs a global langgraph
mock; this test file swaps it out per-test via the ``lt_real``
fixture so the wrapper delegates to the real ``ToolNode``).

No production code changes — these tests assert the SHAPE that the
attestation tool contract must hold under the merge gate.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from tests.helpers.long_tool_nudge import lt_real

# Repo root: tests/unit/tools/test_attestation_surface_chain.py -> parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]
ATTESTATION_PY = REPO_ROOT / "daemon" / "tools" / "attestation.py"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _run_through_wrapped_node(lt, tools, state, config):
    """Build a state graph with a SINGLE node — the wrapped tools
    node carrying the given tool(s) — and run it via the standard
    ``ainvoke`` pattern from
    ``tests/unit/services/test_long_tool_nudge_observability.py``.

    ``tools`` is a list of tools (one or many). Returns the
    updated state dict; the ToolMessage(s) produced by the tool
    invocation are the LAST entries in ``state["messages"]``.
    """
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    if not isinstance(tools, list):
        tools = [tools]

    registry = lt.LongToolNudgeRegistry()
    node = lt.wrapped_tools_node(tools, registry)

    class S(TypedDict, total=False):
        messages: list

    g = StateGraph(S)
    g.add_node("tools", node)
    g.add_edge(START, "tools")
    g.add_edge("tools", END)
    return await g.compile().ainvoke(state, config=config)


def _state_with_attest_ai(
    content: str, call_id: str = "call-attest-1"
) -> dict:
    """Build a state dict whose single AIMessage carries an
    ``attest_completion`` tool_call. The ``content`` parameter is
    the AIMessage's text content (empty for the clean case,
    non-empty for the bundled case).
    """
    return {
        "messages": [
            AIMessage(
                content=content,
                tool_calls=[
                    {
                        "name": "attest_completion",
                        "args": {},
                        "id": call_id,
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }


_CONFIG = {"configurable": {"thread_id": "inst-attest-chain"}}


# ─────────────────────────────────────────────────────────────────────────────
# R1 + R2 — REAL-CHAIN HOOK through wrapped_tools_node
# ─────────────────────────────────────────────────────────────────────────────


class TestAttestCompletionChainHook:
    """The runtime hook in ``daemon/services/long_tool_nudge.py``
    stamps the per-task ``ContextVar`` with the most-recent
    AIMessage BEFORE delegating to the bare ``ToolNode``. The tool
    body then reads that ContextVar to pick the clean vs bundled
    teacher text. These tests pin the FULL chain through the real
    wrapper — not the direct-setter shortcut the existing
    ``test_attestation_tool.py`` tests use.
    """

    @pytest.fixture
    def _clear_caller_state(self):
        """Reset the per-task ContextVar to empty before AND after
        each test. The chain tests below rely on the ContextVar
        being un-stamped at entry (the runtime hook is what stamps
        it; the test verifies the hook actually fires).
        """
        from daemon.tools.attestation import (
            reset_attest_caller_content_for_tests,
        )

        reset_attest_caller_content_for_tests()
        yield
        reset_attest_caller_content_for_tests()

    @pytest.mark.asyncio
    async def test_bundled_chain_returns_bundled_teacher_text(
        self, lt_real, _clear_caller_state
    ) -> None:
        """R1 — Bundled AIMessage through wrapped_tools_node yields
        ``ATTEST_BUNDLED_RESULT_TEXT``. Closes the regression class
        from incident c5d9a38a: a leader bundled the report +
        attest into ONE AIMessage; the runtime hook stamps the
        ContextVar, the tool body reads it, and the resulting
        ToolMessage carries the rejection teacher text the leader
        reads verbatim to issue the report as its own standalone
        message.
        """
        from daemon.tools.attestation import (
            ATTEST_BUNDLED_RESULT_TEXT,
            create_attestation_tools,
        )

        attest_tool = create_attestation_tools()[0]
        # Pre-condition: the runtime hook has not yet fired (the
        # ContextVar is empty by the reset above). The chain test
        # proves the hook fires through the wrapper — NOT by
        # calling the setter directly.
        state = _state_with_attest_ai(
            content="Here is the full report ... attesting now.",
        )
        result = await _run_through_wrapped_node(
            lt_real, attest_tool, state, _CONFIG
        )
        # The tool produced ONE ToolMessage — the last entry.
        tool_message = result["messages"][-1]
        # The ToolMessage carries the tool_call_id from the AIMessage.
        assert tool_message.tool_call_id == "call-attest-1"
        # AND the content is the bundled-shape teacher text.
        assert tool_message.content == ATTEST_BUNDLED_RESULT_TEXT, (
            "R1 FAIL: bundled AIMessage routed through "
            "wrapped_tools_node did not produce the bundled "
            "teacher text on the resulting ToolMessage. The "
            "runtime hook's ContextVar stamp did not propagate "
            "into the tool body, OR the tool body failed to read "
            "it. Re-check daemon/services/long_tool_nudge.py:638-690 "
            "(hook) and daemon/tools/attestation.py:385-396 (tool "
            "body resolver)."
        )
        # Belt-and-braces: leading-text pinning so a future drift
        # that swaps the constant for a synonym does not silently
        # re-open the c5d9a38a shape.
        assert (
            "Attestation recorded, but your tool-call message "
            "contained text" in tool_message.content
        )
        assert (
            "Re-issue your full detailed final report now as its "
            "own standalone message." in tool_message.content
        )

    @pytest.mark.asyncio
    async def test_clean_chain_returns_clean_teacher_text(
        self, lt_real, _clear_caller_state
    ) -> None:
        """R2 — Empty-content AIMessage through wrapped_tools_node
        yields ``ATTEST_CLEAN_RESULT_TEXT``. The hook stamps the
        empty content, the tool body reads ``""``, and the tool
        returns the clean teacher text the leader reads verbatim
        to deliver the report as its subsequent standalone
        message.
        """
        from daemon.tools.attestation import (
            ATTEST_CLEAN_RESULT_TEXT,
            create_attestation_tools,
        )

        attest_tool = create_attestation_tools()[0]
        state = _state_with_attest_ai(content="")
        result = await _run_through_wrapped_node(
            lt_real, attest_tool, state, _CONFIG
        )
        tool_message = result["messages"][-1]
        assert tool_message.tool_call_id == "call-attest-1"
        assert tool_message.content == ATTEST_CLEAN_RESULT_TEXT, (
            "R2 FAIL: empty-content AIMessage routed through "
            "wrapped_tools_node did not produce the clean teacher "
            "text. Either the hook failed to set ContextVar(''), "
            "or the tool body failed to fall through to the clean "
            "path. Re-check daemon/tools/attestation.py:386 "
            "(the ``if caller_content.strip():`` guard) and "
            "daemon/services/long_tool_nudge.py:673-674 (the "
            "hook's empty-content stamping)."
        )
        # Belt-and-braces.
        assert "Attestation recorded." in tool_message.content
        assert (
            "Now deliver your full detailed final report as your "
            "final message" in tool_message.content
        )
        assert "standalone message with no tool calls" in tool_message.content

    @pytest.mark.asyncio
    async def test_mixed_batch_other_tool_does_not_stamp_attest(
        self, lt_real, _clear_caller_state
    ) -> None:
        """A mixed batch carrying a non-attest tool_call FIRST and
        an ``attest_completion`` tool_call SECOND must still stamp
        only the attest slot. The hook (long_tool_nudge.py:667-675)
        iterates tool_calls and stamps ONLY when ``tc_name ==
        \"attest_completion\"``; the early ``break`` exits at the
        first match — the second match would re-stamp the same
        AIMessage. This test pins: the hook ignores non-attest
        tool_calls in the batch (so a leader's normal tool batch
        that happens to also contain attest does not double-stamp
        or cross-contaminate).
        """
        # Define a sibling tool to ride in the same batch as
        # attest_completion — proves the hook filters by tool_name.
        from langchain_core.tools import tool as langchain_tool

        from daemon.tools.attestation import (
            ATTEST_CLEAN_RESULT_TEXT,
            create_attestation_tools,
        )

        @langchain_tool
        def _sibling_tool() -> str:
            """Sibling tool — runs first in the batch."""
            return "sibling-ok"

        # Build a batch with TWO tool_calls in ONE AIMessage:
        # sibling first, attest second (ToolNode runs them in
        # batch — order of execution is the order of declaration).
        attest_tool = create_attestation_tools()[0]
        state = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "_sibling_tool",
                            "args": {},
                            "id": "call-sibling",
                            "type": "tool_call",
                        },
                        {
                            "name": "attest_completion",
                            "args": {},
                            "id": "call-attest-2",
                            "type": "tool_call",
                        },
                    ],
                )
            ]
        }
        result = await _run_through_wrapped_node(
            lt_real, [attest_tool, _sibling_tool], state, _CONFIG
        )
        # Both tool_calls should produce ToolMessages.
        tool_messages = [
            m for m in result["messages"]
            if getattr(m, "type", "") == "tool"
        ]
        assert len(tool_messages) == 2
        # The ToolMessage for attest_completion carries the CLEAN
        # teacher text (the AIMessage was empty-content — the
        # sibling tool_call does not change the AIMessage content).
        attest_msg = next(
            m for m in tool_messages
            if getattr(m, "tool_call_id", "") == "call-attest-2"
        )
        assert attest_msg.content == ATTEST_CLEAN_RESULT_TEXT
        sibling_msg = next(
            m for m in tool_messages
            if getattr(m, "tool_call_id", "") == "call-sibling"
        )
        assert sibling_msg.content == "sibling-ok"


# ─────────────────────────────────────────────────────────────────────────────
# R3 — DEGRADATION: ContextVar unavailable → safe-default CLEAN
# ─────────────────────────────────────────────────────────────────────────────


class TestAttestCompletionDegradation:
    """When the runtime hook never fires — fresh ``ContextVar``
    (default ``\"\"``), ``reset_attest_caller_content_for_tests`` has
    been called, no caller-AIMessage is in scope — the tool body
    MUST return ``ATTEST_CLEAN_RESULT_TEXT`` (the safe default).

    The reset helper is the test-only public API; production code
    never invokes it. Production's natural \"no hook fires\"
    degenerate (e.g. a misconfigured runtime import) collapses to
    the same safe default — pinned here.
    """

    @pytest.fixture
    def _clear_caller_state(self):
        from daemon.tools.attestation import (
            reset_attest_caller_content_for_tests,
        )

        reset_attest_caller_content_for_tests()
        yield
        reset_attest_caller_content_for_tests()

    def test_explicit_reset_then_invoke_returns_clean_teacher_text(
        self, _clear_caller_state
    ) -> None:
        """R3 — ``reset_attest_caller_content_for_tests()`` followed
        by a direct ``attest_completion.invoke({})`` MUST return
        ``ATTEST_CLEAN_RESULT_TEXT``. Pins the safe-default contract
        when the runtime hook never fires — production's natural
        degenerate also collapses to this path.
        """
        from daemon.tools.attestation import (
            ATTEST_CLEAN_RESULT_TEXT,
            attest_completion,
            reset_attest_caller_content_for_tests,
        )

        # Reset, then verify the ContextVar IS empty.
        reset_attest_caller_content_for_tests()
        # Invoke — ContextVar empty ⇒ clean teacher text.
        result = attest_completion.invoke({})
        assert result == ATTEST_CLEAN_RESULT_TEXT, (
            "R3 FAIL: a fresh / reset ContextVar did not yield "
            "the safe-default clean teacher text. Either the "
            "ContextVar default is non-empty (regress "
            "daemon/tools/attestation.py:185-187) or the tool body "
            "failed to map empty to clean (regress "
            "daemon/tools/attestation.py:386 — the "
            "``caller_content.strip()`` truthy guard)."
        )

    def test_degradation_is_safe_when_no_runtime_hook_present(
        self, _clear_caller_state
    ) -> None:
        """R3 — a \"runtime hook absent\" simulate: even after the
        ContextVar has been reset to its default, the tool's return
        is the safe default. Documents that the tool never raises
        a NullPointer/AttributeError when its caller-detection
        state is unset — that would be a regression because
        production degenerate (hook import failure, see
        ``long_tool_nudge.py:676-690``) lands in the same path.
        """
        from daemon.tools.attestation import attest_completion

        # Multiple invocations all return the safe default — no
        # first-call-wins, no exception, no NonDeterminism.
        results = [attest_completion.invoke({}) for _ in range(5)]
        assert all(r.startswith("Attestation recorded.") for r in results), (
            f"R3 FAIL: degradation default is not the clean-call "
            f"teacher text. Got: {results!r}"
        )
        # All five should be IDENTICAL (deterministic default).
        assert len(set(results)) == 1, (
            "R3 FAIL: degradation default is non-deterministic — "
            "every invocation of the safe default must produce "
            "the same teacher text."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Defense-in-depth — STACK-INSPECTOR FALLBACK
# ─────────────────────────────────────────────────────────────────────────────


class TestAttestCompletionStackInspectorFallback:
    """The tool body falls back to ``_fallback_extract_attest_caller_
    content`` (an ``inspect.stack`` walker) when the runtime-set
    ``ContextVar`` is empty. The fallback is documented as
    \"defense-in-depth for test-only call sites\" (see
    ``daemon/tools/attestation.py:248-291``).

    The fallback is reachable only when:
      (a) the runtime hook never set the ContextVar (reset, fresh
          context, or production degenerate), AND
      (b) the calling stack contains an AIMessage with an
          ``attest_completion`` tool_call in some frame's local
          vars (e.g. a unit test that constructs the AIMessage and
          calls the tool without going through wrapped_tools_node).

    These tests pin BOTH branches of the fallback — its positive
    detection (bundled AIMessage in caller locals ⇒ BUNDLED text)
    and its empty-default (no AIMessage in caller locals ⇒ CLEAN
    text, identical to the runtime-set-ContextVar default).
    """

    @pytest.fixture
    def _clear_caller_state(self):
        from daemon.tools.attestation import (
            reset_attest_caller_content_for_tests,
        )

        reset_attest_caller_content_for_tests()
        yield
        reset_attest_caller_content_for_tests()

    def test_fallback_detects_bundled_aimessage_in_caller_locals(
        self, _clear_caller_state
    ) -> None:
        """Positive detection — a caller frame whose locals
        contain an AIMessage with bundled text + an
        ``attest_completion`` tool_call, calling
        ``attest_completion.invoke({})`` WITHOUT going through the
        runtime hook, MUST still receive the BUNDLED teacher text
        via the stack-inspector fallback.

        This pins the documented contract that
        ``_fallback_extract_attest_caller_content`` returns the
        bundled shape when it finds a matching AIMessage in the
        stack. Direct unit tests (which bypass wrapped_tools_node)
        rely on this fallback; without it, every direct-call test
        would land on the CLEAN default and silently mis-report
        the contract.
        """
        from daemon.tools.attestation import (
            ATTEST_BUNDLED_RESULT_TEXT,
            attest_completion,
        )

        # Construct the AIMessage with bundled content + attest
        # tool_call — exactly the c5d9a38a shape.
        bundled_ai = AIMessage(
            content="Full report here. Attesting now.",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "t1"}
            ],
        )
        # The AIMessage lives in this frame's locals (the
        # ``bundled_ai`` binding). Calling the tool from this
        # frame without setting the ContextVar MUST trigger the
        # stack-inspector fallback — the AIMessage is visible in
        # ``inspect.stack()[1].frame.f_locals[\"bundled_ai\"]``.
        result = attest_completion.invoke({})
        assert result == ATTEST_BUNDLED_RESULT_TEXT, (
            "Stack-inspector fallback FAIL: a bundled AIMessage "
            "in the caller frame's locals did not produce the "
            "BUNDLED teacher text via "
            "_fallback_extract_attest_caller_content. Either the "
            "inspect.stack() walker cannot reach the test frame "
            "(regress daemon/tools/attestation.py:272-290) or the "
            "_try_extract_attest_ai_content_from_locals filter "
            "rejects AIMessages whose tool_calls is the dict-shape "
            "literal (regress daemon/tools/attestation.py:296-315)."
        )

    def test_fallback_returns_empty_when_no_aimessage_in_caller_locals(
        self, _clear_caller_state
    ) -> None:
        """Empty detection — a caller frame whose locals contain
        NO AIMessage with an ``attest_completion`` tool_call MUST
        yield the CLEAN teacher text via the fallback (the safe
        default — the fallback returns ``\"\"`` and the tool body
        maps empty to clean).
        """
        from daemon.tools.attestation import (
            ATTEST_CLEAN_RESULT_TEXT,
            attest_completion,
        )

        # No AIMessage in this frame's locals. The fallback's
        # ``_try_extract_attest_ai_content_from_locals`` walks the
        # locals and finds nothing ⇒ returns ``\"\"`` ⇒ tool body
        # maps empty to clean.
        irrelevant_string = "no AIMessage here, just text"
        assert "AIMessage" not in locals() or not any(
            isinstance(v, AIMessage) for v in locals().values()
        ), "Test fixture invariant: this frame must not have any AIMessage in locals"
        result = attest_completion.invoke({})
        assert result == ATTEST_CLEAN_RESULT_TEXT
        # Keep the locals binding live so the test reads as
        # documented; an empty locals dict would still exercise the
        # fallback but the binding documents the intent.
        assert irrelevant_string == "no AIMessage here, just text"


# ─────────────────────────────────────────────────────────────────────────────
# Static contract pins — the verbatim contract lead sentence
# ─────────────────────────────────────────────────────────────────────────────


class TestAttestCompletionToolDescriptionLead:
    """Pins the 2026-09-19 user-decision verbatim contract lead: the
    tool description MUST lead with the exact contract sentence (the
    LLM reads this first at tool-listing time). The fragments are
    pinned IN-ORDER as a single verbatim sentence at the head of
    ``attest_completion``'s docstring AND ``_full_doc_`` (byte-
    exact, char-for-char, no preceding content, no drift in
    punctuation or casing).

    The fragment-pinning in
    ``test_attestation_prompt_contract.py::TestAttestationToolDocstring
    ::test_attest_first_docstring_fragment_present`` covers the
    presence of substrings (case-insensitive). This class pins the
    POSITION-and-order contract — the verbatim sentence MUST be
    the first sentence of the tool description, AND of
    ``_full_doc_``, with no characters preceding it.
    """

    # Verbatim, char-for-char, up to and including the first period.
    _CONTRACT_LEAD = (
        "Call this tool ALONE in one turn - the message containing "
        "this call must contain nothing else (no report, no "
        "commentary). Then deliver your full detailed final report "
        "as your final standalone message."
    )

    def test_attest_completion_docstring_leads_with_contract(self) -> None:
        """The tool's own docstring MUST lead with the contract
        sentence — verbatim, char-for-char, no characters preceding
        it (other than the opening triple-quote and a newline that
        Python attaches to a multi-line docstring opener).
        """
        from daemon.tools.attestation import attest_completion

        doc = attest_completion.description
        # Strip the leading newline that LangChain's ``@tool``
        # decorator appends to the description — the contract lead
        # follows it. If the description is empty, fail loudly.
        assert doc, (
            "attest_completion.description is empty — the tool "
            "lost its description entirely (regress the @tool "
            "decorator or the docstring)."
        )
        assert doc.startswith(self._CONTRACT_LEAD), (
            "attest_completion.description does NOT lead with the "
            "verbatim contract sentence. The LLM reads the FIRST "
            "characters of the description at tool-listing time; "
            "any drift from the verbatim opener silently lets "
            "leaders revert to bundling (the c5d9a38a shape). "
            "Restore the verbatim lead in daemon/tools/"
            "attestation.py:363-364."
        )

    def test_attest_completion_full_doc_leads_with_contract(self) -> None:
        """The ``_full_doc_`` attribute MUST also lead with the
        verbatim contract sentence. LangChain exposes
        ``_full_doc_`` as the rich doc for some surfaces; the
        2026-09-19 user-decision contract lead applies to BOTH
        surfaces — drift in either is a regression.
        """
        from daemon.tools.attestation import attest_completion

        full_doc = getattr(attest_completion, "_full_doc_", "")
        assert full_doc, (
            "attest_completion._full_doc_ is empty — the tool "
            "lost its rich doc surface (regress the @tool "
            "decorator or the _full_doc_ assignment in "
            "daemon/tools/attestation.py:441)."
        )
        assert full_doc.startswith(self._CONTRACT_LEAD), (
            "attest_completion._full_doc_ does NOT lead with the "
            "verbatim contract sentence. Both surfaces "
            "(description + _full_doc_) MUST lead with the same "
            "verbatim opener; drift in either silently lets "
            "leaders revert to bundling. Restore the verbatim "
            "lead in daemon/tools/attestation.py:442."
        )

    def test_teacher_constants_byte_exact_vs_module_constants(self) -> None:
        """The two teacher constants MUST be byte-exact — a
        future drift that re-wraps the constants with whitespace,
        ellipses, or synonym text silently re-opens the
        \"LLM interprets the leading text differently\" regression.
        Pin the byte-exact strings inline (the canonical home is
        the constants block at
        ``daemon/tools/attestation.py:143-159``).
        """
        from daemon.tools import attestation

        # Inline the expected constants to catch ANY drift
        # (whitespace, period, comma — even a single character
        # difference fails the test).
        expected_clean = (
            "Attestation recorded. Now deliver your full detailed "
            "final report as your final message - a standalone "
            "message with no tool calls. (Your attestation call "
            "must contain no text.)"
        )
        expected_bundled = (
            "Attestation recorded, but your tool-call message "
            "contained text - the attestation call must be "
            "text-free. Re-issue your full detailed final report "
            "now as its own standalone message."
        )
        assert attestation.ATTEST_CLEAN_RESULT_TEXT == expected_clean, (
            "ATTEST_CLEAN_RESULT_TEXT drifted from the canonical "
            "byte-exact string. Re-check daemon/tools/"
            "attestation.py:143-147."
        )
        assert (
            attestation.ATTEST_BUNDLED_RESULT_TEXT
            == expected_bundled
        ), (
            "ATTEST_BUNDLED_RESULT_TEXT drifted from the "
            "canonical byte-exact string. Re-check daemon/tools/"
            "attestation.py:154-159."
        )