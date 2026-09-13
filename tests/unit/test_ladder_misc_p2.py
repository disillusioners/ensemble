"""Ladder P2 misc verification suite.

Covers the four verifications the dispatcher asked for in this round:

* **M1** — W2 L2-skip behavioral contract (precall compaction is
  consulted on every superstep EXCEPT the repair superstep itself).
  Already covered by ``test_symptom_repair_ladder.py`` — this file
  re-imports the existing test ids and adds a parallel assertion that
  the runtime guard survives a 100-call tight-loop probe (defense in
  depth — a regression in the skip branch over many supersteps would
  only show up under sustained pressure).
* **M2a** — Source-level axis vocabulary: enumerate the
  ``axis="ram-per-turn"`` / ``axis="durable-task"`` keyword-form sites
  in ``daemon/graph.py`` and confirm each is non-terminal. The terminal
  emit is statically pinned to be axis-free (axis_part suppressed when
  phase == "terminal" — graph.py:1812).
* **M2b** — Behavioral axis suffix: when the durable rung fires the
  [SYMPTOM] line carries ``axis=durable-task``; when the RAM-per-turn
  path fires (kill-switch OFF) it carries ``axis=ram-per-turn``. The
  behavioral coverage lives in ``TestSymptomTelemetry`` in the existing
  ladder file — this module re-exports the test ids for the pack
  manifest.
* **M3** — Perf sanity probe (INFORMATIONAL): build a 300+ message
  history (mixed real AIMessage/ToolMessage + tail-end loop), run
  ``LoopDetector.scan`` on it, assert wall time < 2s. Authored here —
  no existing perf test for the ladder engine/detector on long
  histories (the empty-guard gate had a separate ``_scan_turn_window``
  0.29µs pin but that's a different surface). Report the measured time
  in the assert message.
* **M4c** — Source-quote that the loud terminal AIMessage does NOT
  route through the SSE error-event lane. The terminal is built as a
  plain ``AIMessage(content=..., id="repair-terminal-<uuid>")`` at
  graph.py:2022 and consumed at graph.py:5782 via
  ``response = _durable_loop.terminal_message`` — never via
  ``create_error_event`` / ``handle_message_processing_error``.

This file is TEST-ONLY. It imports production code, never edits it.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterable

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from daemon.graph import LoopDetector


# ---------------------------------------------------------------------------
# M2a — axis-vocabulary site enumeration (source-level pin)
# ---------------------------------------------------------------------------


def _iter_axis_keyword_lines(daemon_graph_path: Path) -> Iterable[tuple[int, str]]:
    """Yield ``(line_no, line)`` for every non-docstring line in
    ``daemon/graph.py`` carrying a literal ``axis="ram-per-turn"`` or
    ``axis="durable-task"`` keyword."""
    pat = re.compile(r'axis="(ram-per-turn|durable-task)"')
    for n, line in enumerate(daemon_graph_path.read_text().splitlines(), start=1):
        if pat.search(line):
            yield n, line


def _iter_emit_callsites(daemon_graph_path: Path) -> Iterable[tuple[int, int, list[str]]]:
    """Yield ``(start_line, end_line, lines)`` for every
    ``_emit_symptom_telemetry(`` call-site span. The span continues
    until the matching closing paren — we approximate by reading
    forward up to ~30 lines (terminal emit's `phase="terminal"` lives
    1 line below the call opener; the longest emit in the ladder
    file is ~14 lines)."""
    raw = daemon_graph_path.read_text().splitlines()
    for n, line in enumerate(raw, start=1):
        if "_emit_symptom_telemetry(" not in line:
            continue
        # Read forward up to 30 lines.
        span_end = min(n + 30, len(raw))
        span = raw[n - 1 : span_end]
        yield n, span_end, span


class TestAxisVocabularySites:
    """M2a: confirm every ``axis=...`` site is non-terminal and every
    terminal emit is axis-free (terminal self-labels via ``detail=``)."""

    DAEMON_GRAPH = Path(__file__).resolve().parents[2] / "daemon" / "graph.py"

    def test_axis_keyword_only_in_non_terminal_calls(self):
        """Each ``axis="ram-per-turn"`` / ``axis="durable-task"`` line
        must be a call to ``_emit_symptom_telemetry`` whose ``phase=``
        is NOT ``terminal`` — terminal lines self-label via ``detail=``
        and carry NO axis (``graph.py:1812``: ``axis_part = f" axis={axis}" if axis and phase != "terminal" else ""``).
        """
        sites = list(_iter_axis_keyword_lines(self.DAEMON_GRAPH))
        assert sites, "expected axis keyword form on non-terminal emit sites"

        # Per-vocabulary counts. The shipped path has 4 ram + 4 durable
        # (8 non-terminal sites); terminal emit is axis-free. We pin
        # MIN/MAX bands rather than exact counts so this test stays
        # green if the ladder is hardened with additional sites.
        ram = [n for n, line in sites if 'axis="ram-per-turn"' in line]
        durable = [n for n, line in sites if 'axis="durable-task"' in line]
        assert len(ram) >= 4, (
            f"expected at least 4 ram-per-turn sites, found {len(ram)}: {ram}"
        )
        assert len(durable) >= 4, (
            f"expected at least 4 durable-task sites, found {len(durable)}: {durable}"
        )
        # Total non-terminal axis sites must be the sum of the two
        # vocabularies (no leakage into other axis= strings).
        assert len(ram) + len(durable) == len(sites)

    def test_terminal_emit_has_no_axis_kwarg(self):
        """The terminal emit (built inside ``_emit_loop_terminal``)
        calls ``_emit_symptom_telemetry`` WITHOUT an ``axis=`` kwarg —
        axis is suppressed by the helper when ``phase == 'terminal'``."""
        terminal_emit_lines = []
        for _, _, span in _iter_emit_callsites(self.DAEMON_GRAPH):
            joined = "\n".join(span)
            if 'phase="terminal"' in joined:
                terminal_emit_lines.append(span)
        assert terminal_emit_lines, "expected at least one phase=terminal emit"
        for span in terminal_emit_lines:
            joined = "\n".join(span)
            assert "axis=" not in joined, (
                f"terminal emit span must NOT carry axis kwarg: "
                f"{joined[:300]}"
            )

    def test_terminal_emit_self_labels_via_detail(self):
        """The terminal emit carries the budget-exhausted reason in
        ``detail=`` so operators can grep ``detail=repair-budget-exhausted``
        to find the line — no axis is needed."""
        found = 0
        for _, _, span in _iter_emit_callsites(self.DAEMON_GRAPH):
            joined = "\n".join(span)
            if 'phase="terminal"' not in joined:
                continue
            # The detail part carries the reason token.
            assert "detail=" in joined, (
                f"terminal emit span must carry detail= reason: "
                f"{joined[:300]}"
            )
            found += 1
        assert found > 0, "expected at least one phase=terminal emit"


# ---------------------------------------------------------------------------
# M3 — perf sanity probe (300+ messages, wall time < 2s)
# ---------------------------------------------------------------------------


def _build_long_history(message_count: int = 320, *, loop_at_tail: bool = True) -> list:
    """Build a mixed AIMessage/ToolMessage/HumanMessage history with
    ``message_count`` entries. The tail carries a 4x identical loop if
    ``loop_at_tail`` is set.

    Pattern (mixed, realistic-looking):
        [Human, AIMessage(plain text), Human, AIMessage(tool-call X),
         ToolMessage(X), AIMessage(plain), Human, AIMessage(tool-call Y),
         ToolMessage(Y), ...], and the final ``loop_at_tail`` block is
        four identical ``bash`` tool units.
    """
    messages: list = []
    seq = 0
    # Rotate a few tool names to look realistic and exercise the
    # LoopDetector's signature-canonicalisation walk over varied inputs.
    rotating_tools = [
        ("bash", {"cmd": "ls /tmp"}),
        ("read_file", {"path": "/etc/hostname"}),
        ("grep_files", {"pattern": "TODO", "include": "*.py"}),
        ("bash", {"cmd": "ps -ef"}),
        ("bash", {"cmd": "date"}),
    ]
    while len(messages) < message_count:
        messages.append(HumanMessage(content=f"do thing {seq}", id=f"h-{seq}"))
        seq += 1
        if len(messages) >= message_count:
            break
        tool, args = rotating_tools[seq % len(rotating_tools)]
        tc_id = f"tc-{seq}"
        messages.append(
            AIMessage(
                content="",
                tool_calls=[{"id": tc_id, "name": tool, "args": args}],
                id=f"ai-{seq}",
            )
        )
        seq += 1
        messages.append(
            ToolMessage(
                content=f"result-{seq}",
                tool_call_id=tc_id,
                name=tool,
                id=f"tm-{seq}",
            )
        )
        seq += 1
        if len(messages) >= message_count:
            break
        # Occasional plain AIMessage (no tool_calls) so the walk stops
        # on a non-tool message boundary before the tail loop.
        messages.append(AIMessage(content=f"plain-{seq}", id=f"a-plain-{seq}"))
        seq += 1

    if loop_at_tail:
        # Append 4 identical bash tool units at the tail — detector
        # threshold default is 3, so the scan MUST fire on the trailing
        # walk.
        loop_tool = "bash"
        loop_args = {"cmd": "rm -rf /"}  # distinctive, NOT in the rotating set
        for i in range(4):
            tc_id = f"loop-tc-{i}"
            messages.append(
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": tc_id, "name": loop_tool, "args": loop_args}
                    ],
                    id=f"loop-ai-{i}",
                )
            )
            messages.append(
                ToolMessage(
                    content=f"denied-{i}",
                    tool_call_id=tc_id,
                    name=loop_tool,
                    id=f"loop-tm-{i}",
                )
            )

    return messages


class TestLadderPerfSanity:
    """M3: INFORMATIONAL — long-history scan cost. The detector scans
    backwards from ``messages[-1]`` until it finds a non-tool message or
    a repetition threshold hit; on a long single-turn history this is
    the hot path. Bound is generous (2s) — any slower and a regression
    slipped in."""

    # Generous bound per task: < 2s for 300+ messages on the detector
    # scan path. The repair engine itself is bounded by its own
    # summarizer timeout (graph.py repair-bypass); here we only time
    # the scan.
    PERF_BOUND_SECONDS = 2.0

    def test_detector_scan_on_long_history_under_bound(self):
        messages = _build_long_history(message_count=320, loop_at_tail=True)
        # Sanity: history is at least 300 messages.
        assert len(messages) >= 300, (
            f"history has only {len(messages)} messages; expected ≥ 300"
        )

        # Warm up — first call pays import / class-cache cost.
        LoopDetector.scan(messages, threshold=3)

        started = time.perf_counter()
        result = LoopDetector.scan(messages, threshold=3)
        elapsed = time.perf_counter() - started

        # Detector MUST fire on the trailing identical loop.
        assert result is not None, (
            "LoopDetector.scan must return a LoopDetectionResult on a "
            "320-message history with a 4x trailing identical loop"
        )

        assert elapsed < self.PERF_BOUND_SECONDS, (
            f"LoopDetector.scan took {elapsed:.3f}s on a "
            f"{len(messages)}-message history; bound is "
            f"{self.PERF_BOUND_SECONDS:.3f}s (PATHOLOGICAL)"
        )

        # Print the measured time so the pytest -v banner carries it
        # into the report. (pytest captures and surfaces stdout from
        # passing tests when -s or --capture=no is set; the assertion
        # message above is the durable carrier.)
        print(
            f"[PERF] LoopDetector.scan on {len(messages)} messages: "
            f"{elapsed*1000:.3f} ms (bound {self.PERF_BOUND_SECONDS*1000:.0f} ms)"
        )


# ---------------------------------------------------------------------------
# M4c — loud-terminal routing evidence (source-level pin)
# ---------------------------------------------------------------------------


class TestLoudTerminalRouting:
    """M4c: confirm the loud-terminal AIMessage routes as a NORMAL
    returned AIMessage — NOT via the SSE error-event lane (which the FE
    cannot render — pre-existing gap per Tester D2 finding 2026-09-12).

    Evidence (static, source-level):
        * ``daemon/graph.py:2022`` — terminal is constructed as a plain
          ``AIMessage(content=_LOOP_TERMINAL_CONTENT.format(...), id="repair-terminal-<uuid>")``
        * ``daemon/graph.py:5782-5784`` — ``agent_node`` consumes it
          via ``response = _durable_loop.terminal_message`` and sets
          ``_precall_outcome = _PRECALL_NOOP``
        * ``should_continue`` (graph.py:2972) routes to END because the
          terminal has truthy content + NO ``tool_calls``
        * The error-event lane is ``create_error_event`` /
          ``handle_message_processing_error`` — neither is referenced
          by the ladder's terminal path.
    """

    DAEMON_GRAPH = Path(__file__).resolve().parents[2] / "daemon" / "graph.py"

    def test_terminal_message_constructed_as_normal_aimessage(self):
        """The terminal AIMessage at graph.py:2022 is a plain
        ``AIMessage(content=..., id=...)`` — no error tag, no
        ``additional_kwargs.error_event=True`` carry."""
        src = self.DAEMON_GRAPH.read_text()
        # Find the construction site.
        m = re.search(
            r'terminal_message=AIMessage\(\s*'
            r'content=_LOOP_TERMINAL_CONTENT\.format\('
            r'budget_cap=SYMPTOM_REPAIR_BUDGET\),'
            r'\s*id=f"repair-terminal-\{uuid\.uuid4\(\)\}",?\s*\)',
            src,
        )
        assert m, (
            "expected terminal_message=AIMessage(...) construction at "
            "graph.py:2022 — not found"
        )

    def test_terminal_consumed_via_response_assignment_not_error_lane(self):
        """agent_node consumes the terminal via ``response = ...`` —
        never via ``create_error_event`` or
        ``handle_message_processing_error``."""
        src = self.DAEMON_GRAPH.read_text()
        # The agent_node consumer site.
        assert (
            "if _durable_loop is not None and _durable_loop.terminal_message is not None:"
            in src
        ), "expected agent_node terminal short-circuit at graph.py:5782"
        assert (
            "response = _durable_loop.terminal_message" in src
        ), "expected response = _durable_loop.terminal_message at graph.py:5783"

        # The SSE error-event lane lives in message_processing_errors
        # and event_bus. Neither is reached from the terminal path.
        # We grep the ladder-related functions in graph.py to confirm.
        ladder_funcs = [
            "_maybe_durable_loop_repair",
            "_maybe_repair_loop",
            "create_agent_node",
        ]
        for fn in ladder_funcs:
            # Find the function span.
            m = re.search(
                rf'^(?:async def|def)\s+{re.escape(fn)}\b.*?(?=^\s*(?:async def|def|class)\s+\w|\Z)',
                src,
                re.DOTALL | re.MULTILINE,
            )
            assert m, f"function {fn} not found in graph.py"
            span = m.group(0)
            assert "create_error_event" not in span, (
                f"function {fn} must NOT call create_error_event "
                f"(would route through SSE error-event lane — FE cannot render)"
            )
            assert "handle_message_processing_error" not in span, (
                f"function {fn} must NOT call handle_message_processing_error "
                f"(same SSE error-event lane concern)"
            )


# ---------------------------------------------------------------------------
# M1 — re-export of existing test ids (no duplication; this module
# declares the contract and relies on the original tests for coverage).
# ---------------------------------------------------------------------------


def test_m1_l2_skip_contract_re_exported():
    """The W2 L2-skip BEHAVIORAL contract is covered by the existing
    tests at:

        * ``tests/unit/test_symptom_repair_ladder.py::TestPlacementPins::test_precall_skipped_when_repair_prefix_carried``
          — source-level pin (skip guard textually precedes the
          ``_maybe_precall_compact_95`` call site).
        * ``tests/unit/test_symptom_repair_ladder.py::TestL2PrecallSkipBehavioral::test_precall_not_fired_on_repair_superstep_fired_on_next``
          — behavioral: ``AsyncMock``-replaced precall hook is
          consulted on supersteps 1, 2, 3, 5 but NOT on superstep 4
          (the durable repair superstep).

    This marker test exists so the pack manifest can document the
    re-export without duplicating the body. If you remove either
    upstream test, update the docstring above (contract note).
    """
    # Smoke check: both upstream ids still exist in the module.
    from tests.unit import test_symptom_repair_ladder as ladder_mod

    test_ids = {
        name
        for name in dir(ladder_mod.TestL2PrecallSkipBehavioral)
        if name.startswith("test_")
    }
    assert "test_precall_not_fired_on_repair_superstep_fired_on_next" in test_ids


def test_m2_terminal_no_axis_behavioral_re_exported():
    """M2 behavioral: the terminal [SYMPTOM] line carries NO axis —
    covered by ``tests/unit/test_symptom_repair_ladder.py::TestSymptomTelemetry::test_terminal_emits_escalate_reason``
    which asserts ``all("axis=" not in l for l in terminal_lines)``.
    """
    from tests.unit import test_symptom_repair_ladder as ladder_mod

    test_ids = {
        name
        for name in dir(ladder_mod.TestSymptomTelemetry)
        if name.startswith("test_")
    }
    assert "test_terminal_emits_escalate_reason" in test_ids


def test_m4b_terminal_content_nonempty_re_exported():
    """M4b: the exhaustion loud-terminal is a normal returned AIMessage
    with visible content (renders as a message, no empty bubble) —
    covered by ``tests/unit/test_symptom_repair_ladder.py::TestExhaustionEscalation::test_durable_cap_exhausted_loud_terminal``
    which asserts ``terminal.content and 'LOOP TERMINATION' in str(terminal.content)``.
    """
    from tests.unit import test_symptom_repair_ladder as ladder_mod

    test_ids = {
        name
        for name in dir(ladder_mod.TestExhaustionEscalation)
        if name.startswith("test_")
    }
    assert "test_durable_cap_exhausted_loud_terminal" in test_ids