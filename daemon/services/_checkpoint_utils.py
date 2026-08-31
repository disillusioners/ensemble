"""Shared checkpoint-state helper — terminal-checkpoint detection.

Phase 1 / WS-2.4 (architect §5, O8 spec pin): the helper detects
terminal checkpoints (``state.next`` empty/None — i.e. the graph has
finished) so BOTH ``instance_messaging._maybe_compact_context`` (the
auto proactive path) and ``compact_executor.execute_compact`` (the
on-demand ``/compact`` path) reject the same way. Sharing the helper
closes the drift window where two sites could silently disagree on
what "terminal" means (a ``aupdate_state`` on a terminal checkpoint
clears the next pointer and bricks the revive-on-send path —
:1132-1140 documented COMPLETED→RUNNING→COMPLETED collapse).

Engine-reuse boundary: this helper lives in a NEW module so the
compaction engine (``daemon/compaction.py``) stays free of
checkpoint-state semantics — it never imports this module. Only the
messaging layer and the executor import.

Source-level invariant (anti-drift, 2.4 acceptance): a grep over
``daemon/`` for ``from daemon.services._checkpoint_utils import``
finds EXACTLY two sites — ``instance_messaging._maybe_compact_context``
and ``compact_executor.execute_compact``. Future additions are
caught by a unit test that walks the AST.
"""
from __future__ import annotations

from typing import Any


def _is_terminal_checkpoint(state: Any) -> bool:
    """Return ``True`` iff ``state.next`` indicates a terminal checkpoint.

    A LangGraph checkpoint is terminal when the graph has no remaining
    work — the ``next`` channel is empty (``()``) or ``None``. Calling
    ``aupdate_state(as_node="agent")`` on a terminal checkpoint clears
    the next pointer, so the subsequent ``astream(graph_input)`` for
    the next message returns instantly without running the agent. On
    reuse of a completed instance this collapses the
    COMPLETED→RUNNING→COMPLETED cycle to <100ms — the FE never sees
    RUNNING, and revive-on-send silently breaks.

    Used by BOTH:

    * ``instance_messaging._maybe_compact_context`` (~:1146) — the
      auto proactive path skips compaction entirely when the
      checkpoint is terminal.
    * ``compact_executor.execute_compact`` — the on-demand ``/compact``
      path REJECTS with ``reason=terminal_instance`` and the guidance
      detail "Send a message to start a new turn, then /compact."

    Args:
        state: The LangGraph state snapshot — typically the result of
            ``graph.aget_state(config)``. ``None`` is treated as
            terminal (no live graph to compact).

    Returns:
        ``True`` when ``state.next`` is falsy (``None``, empty tuple,
        empty list, etc.) — the safe default for "no work pending".
        ``False`` otherwise.
    """
    if state is None:
        return True
    next_attr = getattr(state, "next", None)
    if not next_attr:
        return True
    return False
