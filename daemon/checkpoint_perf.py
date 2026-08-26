"""Structured-ish performance logging for LangGraph checkpoint operations.

PR1 (C4) of the LangGraph Checkpoint / Message Persistence Performance plan.
The module is the single source of truth for the ``[CheckpointPerf]`` and
``[/Messages]`` log lines that PR1 introduces. It is intentionally tiny:
no daemon state, no I/O, no scheduling — pure functions that emit one log
line per call and return their inputs unchanged so the call sites stay
trivial to read.

Risk-mitigation: when the env var ``CHECKPOINT_PERF_LOGS`` is set to a
falsy value (e.g. ``"0"`` / ``"false"`` / ``"no"``) every emit is
suppressed. This keeps the log volume from spiking in production while
still allowing local dev to opt in via the default (no env var).

The verbatim signatures live in
``.agents/shared/planning/langgraph-checkpoint-perf/phase1-plan.md``
lines 98-148; do NOT edit them without a plan revision.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Awaitable

logger = logging.getLogger("daemon.checkpoint_perf")


def _logs_enabled() -> bool:
    """Return True unless ``CHECKPOINT_PERF_LOGS`` is set to a falsy value.

    Treats ``"0"``, ``"false"``, ``"no"``, ``""`` (and any case variant) as
    off. Anything else (unset, ``"1"``, ``"true"``, …) leaves logging on,
    matching the risk-mitigation row at phase1-plan.md:197.
    """
    raw = os.environ.get("CHECKPOINT_PERF_LOGS")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def checkpoint_perf_logs_enabled() -> bool:
    """Public gate for call sites that conditionally pay a hot-path cost (W3).

    The ``log_*`` emitters already no-op when ``CHECKPOINT_PERF_LOGS`` is
    falsy; this wrapper lets callers skip the *computation* behind a
    suppressed emit (e.g. the O(total-content) ``bytes_estimate`` walk in
    ``daemon/persistence.py``) instead of only skipping the log line.
    """
    return _logs_enabled()


def log_saver_op(op: str, thread_id: str, duration_ms: int, *, deleted: int = 0) -> None:
    """Single source of truth for ``[CheckpointPerf]`` structured-ish logs."""
    if not _logs_enabled():
        return
    logger.info(
        f"[CheckpointPerf] op={op} thread={thread_id[:8] if thread_id else '?'} "
        f"duration_ms={duration_ms} deleted={deleted}"
    )


async def time_saver_op(op: str, thread_id: str, coro: Awaitable[Any]) -> Any:
    """Time a saver operation; emits ``[CheckpointPerf]`` and returns the result."""
    t0 = time.perf_counter()
    try:
        return await coro
    finally:
        elapsed = int((time.perf_counter() - t0) * 1000)
        log_saver_op(op, thread_id, elapsed)


def log_messages_api(
    instance_id: str,
    duration_ms: int,
    message_count: int,
    bytes_estimate: int,
    alist_count: int,  # OBSERVED; not a hardcoded constant
) -> None:
    """Emit the GET /messages single-line structured log carrying the observed alist count."""
    if not _logs_enabled():
        return
    logger.info(
        f"[/Messages] instance={instance_id[:8] if instance_id else '?'} "
        f"duration_ms={duration_ms} messages={message_count} bytes={bytes_estimate} "
        f"alist_count={alist_count}"
    )


def log_prune(
    op: str,
    threads: int,
    deleted: int,
    duration_ms: int,
    *,
    max_per_thread: int | None = None,
    note: str = "",
) -> None:
    """Gated emit for the maintenance prune observation lines (W4).

    ``daemon/services/maintenance.py`` routes every ``[CheckpointPerf]
    op=prune*`` line through this helper so ``CHECKPOINT_PERF_LOGS=0``
    suppresses them uniformly — previously these lines wrote to the
    module logger directly and bypassed the env gate. ``op`` is one of
    ``prune`` (no-excess early return), ``prune-entry``, ``prune-exit``.
    """
    if not _logs_enabled():
        return
    line = (
        f"[CheckpointPerf] op={op} threads={threads} "
        f"deleted={deleted} duration_ms={duration_ms}"
    )
    if max_per_thread is not None:
        line += f" max_per_thread={max_per_thread}"
    if note:
        line += f" ({note})"
    logger.info(line)


def log_message_tap(
    thread_id: str,
    count: int,
    source: str,
) -> None:
    """Gated emit for the C2 message-tap observation lines (PR2).

    Phase 1 C2 (``message_metadata`` side table + ``MessageTapSlot``).
    The 4 approved tap sites (decisions.md D1) each emit one line per
    call:

    * ``source="user_message_entry"`` — at ``_build_graph_input``
      (instance_messaging.py; F1 fix).
    * ``source="agent_node_return"`` — post-F2 single-return site in
      ``daemon/graph.py`` (covers both the injected-branch AND the
      plain-turn branch in one call).
    * ``source="compaction_aupdate_reactive"`` — after the reactive
      compaction ``aupdate_state`` at ``daemon/graph.py:3248-3250``.
    * ``source="compaction_aupdate_messaging"`` — after the
      messaging-side ``aupdate_state`` at
      ``daemon/services/instance_messaging.py:810-822``.

    Every emit is suppressed when ``CHECKPOINT_PERF_LOGS`` is set to a
    falsy value, matching the C4 instrumentation. ``count`` is the
    rows-affected returned by ``MessageMetadataRepository.upsert_batch``
    — the rowcount is 0 on a no-op re-tap under
    ``ON CONFLICT DO NOTHING`` (D3).
    """
    if not _logs_enabled():
        return
    logger.info(
        f"[MessageTap] source={source} thread={thread_id[:8] if thread_id else '?'} "
        f"count={count}"
    )


def invariant_check_no_alist() -> None:
    """ERROR log if invoked at all post-C1."""
    # Invariant violation is never suppressed by CHECKPOINT_PERF_LOGS — the
    # whole point is to scream if we ever re-introduce an alist call on the
    # /messages request path post-C1. (The plan calls for an ERROR log, not
    # an exception — observability only.)
    logger.error(
        f"[CheckpointPerf] INVARIANT VIOLATION: alist invoked on request path post-C1; "
        f"see roadmap §6 (Phase 1 gate #2). The expected value is 0 (by absence)."
    )
