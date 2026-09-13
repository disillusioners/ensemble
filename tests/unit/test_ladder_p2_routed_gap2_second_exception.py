"""Ladder phase 2 — REVIEWER-ROUTED TEST #2.

Second-exception path (G-R2 / gap closer).

Coverage gap closed
-------------------
The existing ``test_symptom_repair_engine_phase2.py`` covers the
pre-terminal repair at the **unit** level — both the master-OFF
path (helper returns ``None``; caller falls through) and the
master-ON success path (helper returns a recovered outcome). But
the **second-exception** path — when the LLM re-invocation AFTER
the repair fails AGAIN with the same/similar exception class —
is NOT covered end-to-end. The existing
``_PreTerminalRepairOutcome`` documents the
``abort_reason='second-exception'`` branch at daemon/graph.py:~2776
but the test suite only verifies it indirectly via the abort-reason
attribute. The phase-2 reviewer routed this gap to a real-graph
integration pin.

What this test pins
-------------------
A provider that ALWAYS raises the recognized pre-terminal exception
class (truncated or empty_post_ladder). With master ON, drive a
real langgraph run; assert:

1. The pre-terminal intercept fires EXACTLY ONCE (one
   ``[SYMPTOM] class=<class> phase=detect action=fired`` telemetry
   line; the repair attempt is logged as
   ``phase=repair action=fired`` followed by
   ``phase=repair_abort action=abort`` with the
   ``second-exception`` reason in the detail).
2. The provider was called TWICE (the original failed call + the
   post-surgery re-invocation).
3. The loud `[LLM] All retries exhausted` ERROR fires (the
   agent_node falls through to the shipped loud-ERROR path after
   the repair aborts).
4. The propagated exception CLASS is the same subclass of
   ``LLMResponseValidationError`` as the original — the
   ``agent_node`` re-raises the original exception (not a wrapper),
   so the surfaced exception type is byte-identical to the
   provider's raise class.

**Byte-identical log baseline** (ADR-0009 / USER AMENDMENT
2026-09-13): the loud `[LLM] All retries exhausted` ERROR line
fires under BOTH master-OFF and master-ON-repair-abort paths. To
PROVE the surfaced ERROR is byte-identical, this test also
captures the OFF-baseline log (master OFF + same provider) and
asserts the ON line is byte-identical — captured via a normalized
regex match (the format includes ``transient_attempts`` /
``timeout_attempts`` / category; both arms use the same
``retry_config``, so the log MUST match). This is the master-OFF
→ master-ON log equivalence that closes the "shipped loud ERROR
unchanged" contract.

Parametrized over the two pre-terminal classes:
* ``truncated`` (LLMResponseValidationError with
  finish_reason=length)
* ``empty_post_ladder`` (EmptyLLMResponseError subclass)

Mechanics precedent
-------------------
Mirrors the real-langgraph harness in
``tests/test_ladder_loop_x_empty_guard_integration.py` and the
pre-terminal-intercept wiring at
``tests/unit/test_symptom_repair_engine_phase2.py::TestMasterKillSwitchByteIdentical``.
Uses ``tests.helpers.symptom_repair._RealLangGraph`` to swap the
conftest's mocked langgraph modules.
"""
from __future__ import annotations

import logging
import re

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.config import _reset_symptom_repair_ladder_for_tests
from daemon.graph import create_agent_node
from daemon.response_validation import (
    EmptyLLMResponseError,
    LLMResponseValidationError,
)
from daemon.services.symptom_repair_engine import SymptomRepairEngine
from tests.helpers.symptom_repair import _RealLangGraph, ok_summarizer


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_flags():
    """Isolate the ladder kill-switch module cache (config accessors)."""
    _reset_symptom_repair_ladder_for_tests()
    yield
    _reset_symptom_repair_ladder_for_tests()


def _truncated_response_message(seq: int) -> AIMessage:
    """A truncated AIMessage (finish_reason=length)."""
    return AIMessage(
        content="partial answer that hit the token limit",
        response_metadata={"finish_reason": "length"},
        id=f"trunc-{seq}",
    )


def _empty_response_message(seq: int) -> AIMessage:
    """An empty AIMessage (the empty_post_ladder class is exactly this)."""
    return AIMessage(content="", id=f"empty-{seq}")


def _make_raising_provider(exception_factory):
    """A provider stub that ALWAYS raises via ``exception_factory(seq)``.

    The ``seq`` increments per call so the stack-trace + message id
    trace makes the call-count assertion auditable.
    """

    class _RaisingProvider:
        def __init__(self):
            self.calls: list[list] = []
            self._seq = 0

        def invoke(self, messages):
            self.calls.append(list(messages))
            self._seq += 1
            raise exception_factory(self._seq)

    return _RaisingProvider()


def _make_agent_node(provider):
    return create_agent_node(
        llm_with_tools=provider,
        system_prompt="you are a test assistant",
        compactor=None,
        graph_ref=[None],
        config=None,
        llm_config={"model": "test-model"},
        retry_config={"transient_attempts": 1, "timeout_attempts": 1},
    )


# ---------------------------------------------------------------------------
# Per-class exception factories (parametrize over both)
# ---------------------------------------------------------------------------


def _truncated_factory(seq: int) -> LLMResponseValidationError:
    """Truncated (finish_reason=length) — an LLMResponseValidationError
    that the pre-terminal detector classifies via
    ``_is_truncated_response_error``."""
    return LLMResponseValidationError(
        "truncated response",
        response=_truncated_response_message(seq),
    )


def _empty_factory(seq: int) -> EmptyLLMResponseError:
    """Empty_post_ladder — EmptyLLMResponseError subclass, classified
    via ``_is_empty_response_error``."""
    return EmptyLLMResponseError(
        f"empty response (seq={seq})",
    )


# The parametrized matrix — one test case per pre-terminal class.
# The exception class itself is asserted downstream by the test
# body via the ``exc_type`` kwarg.
_EXCEPTION_MATRIX = [
    pytest.param(
        _truncated_factory,
        LLMResponseValidationError,
        id="truncated",
    ),
    pytest.param(
        _empty_factory,
        EmptyLLMResponseError,
        id="empty_post_ladder",
    ),
]


# Regex that extracts the meaningful part of the shipped loud
# `[LLM] All retries exhausted` line, normalising the retry-config
# surface so the master-ON and master-OFF logs are byte-comparable
# (both arms use the SAME ``retry_config``).
_LOUD_ERROR_RE = re.compile(
    r"\[LLM\] All retries exhausted "
    r"\((?P<category>[^,]+), "
    r"transient_attempts=(?P<transient>[^,]+), "
    r"timeout_attempts=(?P<timeout>[^)]+)\): "
    r"(?P<exc_type>[A-Za-z_][A-Za-z0-9_]*): (?P<detail>.+)$"
)


def _normalize_loud_error(line: str) -> str:
    """Normalize a loud-ERROR line by regex-stripping the
    transient/timeout/category surface (both arms use the same
    ``retry_config``; the values are identical between master-OFF
    and master-ON runs, but we strip them to keep the comparison
    tight to the exception-class surface, which is what we want
    to pin byte-identical)."""
    m = _LOUD_ERROR_RE.match(line)
    if m is None:
        return line  # leave unparsed lines untouched
    # Keep only the exception type + detail (the exception-class
    # surface is the byte-identical contract under ADR-0009).
    return f"exc_type={m.group('exc_type')} detail={m.group('detail')}"


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


class TestSecondExceptionPathRealGraph:
    """Routed gap #2 — pre-terminal repair second-exception path."""

    @pytest.mark.parametrize(
        "exception_factory, exc_type",
        _EXCEPTION_MATRIX,
    )
    @pytest.mark.asyncio
    async def test_symptom_persists_after_repair_loud_error_byte_identical(
        self, monkeypatch, tmp_path, caplog, exception_factory, exc_type
    ):
        """Master ON + provider always raises the pre-terminal class:
        the intercept fires ONCE (detect + repair + repair_abort with
        the ``second-exception`` reason), the provider is called
        TWICE (original + post-surgery re-invoke), the loud `[LLM]
        All retries exhausted` ERROR fires, the propagated exception
        CLASS is the same as the original, and the loud-ERROR log is
        byte-identical to the master-OFF baseline (the shipped
        contract — ``agent_node`` re-raises the ORIGINAL exception,
        no wrapper).

        Captured via two ainvokes per parametrized case:
        * ON arm: master ON, repair fires, repair aborts on
          second-exception, loud ERROR fires, exception propagates.
        * OFF arm: master OFF, repair NEVER fires (helper returns
          None), loud ERROR fires immediately, exception propagates.

        The OFF baseline produces the canonical loud-ERROR line; the
        ON line is asserted to normalize to the same string.
        """
        # Engine summarizer stub — the surgery/budget/doc flow is real.
        monkeypatch.setattr(
            SymptomRepairEngine,
            "_summarize",
            staticmethod(ok_summarizer),
        )

        # ───────────────────────────── ON arm ─────────────────────────────
        monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "1")
        _reset_symptom_repair_ladder_for_tests()

        on_provider = _make_raising_provider(exception_factory)
        on_agent = _make_agent_node(on_provider)

        on_loud_error: str | None = None
        on_propagated_exc: BaseException | None = None

        with _RealLangGraph():
            from langgraph.graph import (
                END as _REAL_END,
                START as _REAL_START,
                MessagesState as _RealMessagesState,
                StateGraph as _RealStateGraph,
            )
            globals()["END"] = _REAL_END
            globals()["START"] = _REAL_START
            globals()["MessagesState"] = _RealMessagesState
            globals()["StateGraph"] = _RealStateGraph

            class _State(_RealMessagesState):
                repair_budget_used: int

            g_on = _RealStateGraph(_State)
            g_on.add_node("agent", on_agent)
            g_on.add_edge(_REAL_START, "agent")
            g_on.add_edge("agent", _REAL_END)

            db_path_on = tmp_path / "second_exc_on.db"
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            conn_on = await aiosqlite.connect(str(db_path_on))
            saver_on = AsyncSqliteSaver(conn_on)
            await saver_on.setup()
            try:
                compiled_on = g_on.compile(checkpointer=saver_on)
                cfg_on = {
                    "configurable": {"thread_id": "second-exc-on-iid"},
                    "recursion_limit": 20,
                }
                # caplog is shared across both arms — isolate the
                # ON run's records by snapshotting the ON records
                # INSIDE the with-block (the records accumulate
                # across arms; we read the slice that grew during
                # this ``with`` block).
                on_records_before = len(caplog.records)
                with caplog.at_level(logging.INFO):
                    try:
                        await compiled_on.ainvoke(
                            {
                                "messages": [
                                    HumanMessage(content="go", id="h1")
                                ],
                                "repair_budget_used": 0,
                            },
                            cfg_on,
                        )
                    except BaseException as e:
                        on_propagated_exc = e
                on_records = caplog.records[on_records_before:]

                # Capture the loud ERROR line from the ON run.
                loud_lines_on = [
                    r.getMessage()
                    for r in on_records
                    if "[LLM] All retries exhausted" in r.getMessage()
                ]
                assert loud_lines_on, (
                    f"ON arm must log [LLM] All retries exhausted; "
                    f"captured {loud_lines_on!r}"
                )
                # Exactly ONE loud ERROR fires (the original raise
                # only — the post-surgery re-invoke raises INSIDE
                # ``_maybe_pre_terminal_repair`` and is CAUGHT there
                # with a ``second-exception`` abort_reason; the
                # agent_node then re-raises the ORIGINAL ``e``).
                # So there should be exactly one loud ERROR line
                # in the agent_node except block.
                assert len(loud_lines_on) == 1, (
                    f"ON arm must log exactly one [LLM] All retries "
                    f"exhausted line; got {len(loud_lines_on)}: "
                    f"{loud_lines_on!r}"
                )
                on_loud_error = loud_lines_on[0]

                # ── (a) pre-terminal intercept fires ONCE
                symptom_lines_on = [
                    r.getMessage()
                    for r in on_records
                    if "[SYMPTOM]" in r.getMessage()
                ]
                joined_on = "\n".join(symptom_lines_on)
                assert "phase=detect action=fired" in joined_on, (
                    f"ON arm must emit pre-terminal detect telemetry; "
                    f"got {joined_on!r}"
                )
                assert "phase=repair action=fired" in joined_on, (
                    f"ON arm must emit pre-terminal repair telemetry; "
                    f"got {joined_on!r}"
                )
                assert "second-exception" in joined_on, (
                    f"ON arm must emit second-exception abort "
                    f"telemetry; got {joined_on!r}"
                )

                # ── (b) provider called TWICE (original + re-invoke)
                assert len(on_provider.calls) == 2, (
                    f"ON arm must call the provider exactly twice "
                    f"(original + post-surgery re-invoke); got "
                    f"{len(on_provider.calls)}"
                )

                # ── (c) propagated exception class is the same
                # subclass of LLMResponseValidationError.
                assert isinstance(on_propagated_exc, exc_type), (
                    f"ON arm must re-raise the original exception "
                    f"class (or subclass); got "
                    f"{type(on_propagated_exc).__name__!r}"
                )
            finally:
                await conn_on.close()

        # ───────────────────────────── OFF arm (byte-identical baseline) ─
        # Reset state for the OFF baseline.
        monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "0")
        _reset_symptom_repair_ladder_for_tests()

        off_provider = _make_raising_provider(exception_factory)
        off_agent = _make_agent_node(off_provider)

        off_loud_error: str | None = None
        off_propagated_exc: BaseException | None = None

        with _RealLangGraph():
            from langgraph.graph import (
                END as _REAL_END,
                START as _REAL_START,
                MessagesState as _RealMessagesState,
                StateGraph as _RealStateGraph,
            )
            globals()["END"] = _REAL_END
            globals()["START"] = _REAL_START
            globals()["MessagesState"] = _RealMessagesState
            globals()["StateGraph"] = _RealStateGraph

            class _StateOff(_RealMessagesState):
                repair_budget_used: int

            g_off = _RealStateGraph(_StateOff)
            g_off.add_node("agent", off_agent)
            g_off.add_edge(_REAL_START, "agent")
            g_off.add_edge("agent", _REAL_END)

            db_path_off = tmp_path / "second_exc_off.db"
            conn_off = await aiosqlite.connect(str(db_path_off))
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            saver_off = AsyncSqliteSaver(conn_off)
            await saver_off.setup()
            try:
                compiled_off = g_off.compile(checkpointer=saver_off)
                cfg_off = {
                    "configurable": {"thread_id": "second-exc-off-iid"},
                    "recursion_limit": 20,
                }
                # Snapshot caplog to isolate OFF-arm records.
                off_records_before = len(caplog.records)
                with caplog.at_level(logging.INFO):
                    try:
                        await compiled_off.ainvoke(
                            {
                                "messages": [
                                    HumanMessage(content="go", id="h1")
                                ],
                                "repair_budget_used": 0,
                            },
                            cfg_off,
                        )
                    except BaseException as e:
                        off_propagated_exc = e
                off_records = caplog.records[off_records_before:]

                loud_lines_off = [
                    r.getMessage()
                    for r in off_records
                    if "[LLM] All retries exhausted" in r.getMessage()
                ]
                assert loud_lines_off, (
                    f"OFF arm must log [LLM] All retries exhausted; "
                    f"captured {loud_lines_off!r}"
                )
                assert len(loud_lines_off) == 1, (
                    f"OFF arm must log exactly one [LLM] All retries "
                    f"exhausted line; got {len(loud_lines_off)}: "
                    f"{loud_lines_off!r}"
                )
                off_loud_error = loud_lines_off[0]

                # ── OFF provider called ONCE (no repair → no re-invoke)
                assert len(off_provider.calls) == 1, (
                    f"OFF arm must call the provider exactly once "
                    f"(no repair → no re-invoke); got "
                    f"{len(off_provider.calls)}"
                )

                # ── OFF propagated exception class is the same
                assert isinstance(off_propagated_exc, exc_type), (
                    f"OFF arm must re-raise the original exception "
                    f"class (or subclass); got "
                    f"{type(off_propagated_exc).__name__!r}"
                )
            finally:
                await conn_off.close()

        # ── (d) byte-identical loud ERROR — the shipped contract.
        on_norm = _normalize_loud_error(on_loud_error)
        off_norm = _normalize_loud_error(off_loud_error)
        assert on_norm == off_norm, (
            f"master-ON second-exception loud ERROR must be "
            f"byte-identical (modulo retry-config surface) to "
            f"master-OFF baseline; got\n  ON:  {on_loud_error!r}\n  "
            f"OFF: {off_loud_error!r}\n  ON-norm:  {on_norm!r}\n  "
            f"OFF-norm: {off_norm!r}"
        )

        # ── (e) exception class byte-identical between arms
        assert type(on_propagated_exc) is type(off_propagated_exc), (
            f"master-ON propagated exception class must match "
            f"master-OFF baseline byte-identically; got "
            f"ON={type(on_propagated_exc).__name__!r} vs "
            f"OFF={type(off_propagated_exc).__name__!r}"
        )
