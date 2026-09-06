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

from daemon.checkpoint_metrics import (
    checkpoint_list_total,
    reset_for_tests,
    saver_op_latency_seconds,
)

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


def log_saver_op(
    op: str,
    thread_id: str,
    duration_ms: int,
    *,
    bytes_: int = 0,
    deleted: int = 0,
) -> None:
    """Single source of truth for ``[CheckpointPerf]`` structured-ish logs.

    FR-5 AC-5.1 (T5.3): every saver op MUST emit one log line of shape
    ``op=<name> latency_ms=<int> bytes=<int>``. Gated by
    ``CHECKPOINT_PERF_LOGS``.

    The full line is::

        [CheckpointPerf] op=<name> thread=<8-char-prefix> latency_ms=<int> \
            bytes=<int> deleted=<int>

    ``thread=`` and ``deleted=`` are diagnostic extras (kept from the
    v1 PR1 surface); the contract-required trio is ``op=`` /
    ``latency_ms=`` / ``bytes=`` — verified by the per-op caplog pin in
    ``tests/unit/persistence/test_checkpoint_perf_logging.py``.

    Metric side-effect (FR-5 AC-5.2): each call also observes the
    ``message_api_saver_op_latency_seconds`` histogram (labeled by
    ``op``). Live-path alist calls (zero post-PR3) would also increment
    ``message_api_checkpoint_list_total`` via
    :func:`increment_checkpoint_list_total` — that is a REGRESSION HOOK
    for if alist ever re-fires on the live path; today the migrator
    (``daemon/migrations/checkpoint_migrator.py``) is the only caller
    that should record alist, and it does NOT call this helper.

    NEVER wrap in ``except BaseException:`` (C-14 — CancelledError
    propagates by design on Python 3.13). The metric observe/inc is
    itself exception-safe (lock + arithmetic; no I/O), so no try/except
    is needed at this layer.
    """
    if not _logs_enabled():
        # Metric records REGARDLESS of the log gate — operator's SLO
        # surface is independent of log volume. Suppressing the log
        # line must not silently starve the histogram.
        saver_op_latency_seconds.labels(op=op).observe(duration_ms / 1000.0)
        return
    logger.info(
        f"[CheckpointPerf] op={op} "
        f"thread={thread_id[:8] if thread_id else '?'} "
        f"latency_ms={duration_ms} bytes={bytes_} deleted={deleted}"
    )
    saver_op_latency_seconds.labels(op=op).observe(duration_ms / 1000.0)


async def time_saver_op(op: str, thread_id: str, coro: Awaitable[Any]) -> Any:
    """Time a saver operation; emits ``[CheckpointPerf]`` and returns the result.

    Forwards ``bytes_=0`` and ``deleted=0`` (the existing
    ``log_saver_op`` defaults) — call sites that have richer info
    (e.g. ``maintenance.py`` carveouts) call :func:`log_saver_op`
    directly with the right kwargs.
    """
    t0 = time.perf_counter()
    try:
        return await coro
    finally:
        elapsed = int((time.perf_counter() - t0) * 1000)
        log_saver_op(op, thread_id, elapsed)


def increment_checkpoint_list_total(amount: int = 1) -> int:
    """Increment the ``message_api_checkpoint_list_total`` counter.

    FR-5 AC-5.2 + FR-2 invariant. Post-PR3 the LIVE path makes ZERO
    ``saver.alist(…)`` calls, so the counter's expected value is 0 (the
    counter is a regression hook — if it ever moves off zero, alist
    fired on a live path; the FR-2 test
    ``tests/integration/test_get_instance_messages_observed_count_zero.py``
    pins this). The migrator
    (``daemon/migrations/checkpoint_migrator.py``) is exempt — it is the
    ONE sanctioned caller of ``saver.alist(…)`` and does NOT call this
    function (the migrator's alist is OFFLINE / non-live).

    Returns the new counter value (convenient for the test harness).
    """
    checkpoint_list_total.inc(amount)
    return checkpoint_list_total.get()


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


def log_blob_prune(
    thread_id: str,
    dry_run: bool,
    deleted: int,
    refs_seen: int,
    *,
    skipped_reason: str | None = None,
    observed_blob_count: int = 0,
    bytes_freed: int = 0,
) -> None:
    """Gated emit for the C3 reference-aware blob-prune observation lines (PR4).

    One line per (thread_id, checkpoint_ns) candidate the prune walks —
    emitted by ``daemon/services/checkpoint_prune.py`` for all four
    outcomes:

    * normal dry-run:  ``dry_run=1 deleted=<would_delete>
      bytes=<bytes_would_free> refs_seen=<n> observed_blob_count=<n>``
      (``deleted`` carries the WOULD-DELETE count, not actual deletions);
    * normal destructive: same line with ``dry_run=0`` and real counts;
    * fail-safe skip: ``skipped_reason=ZERO_REFS_FAIL_SAFE`` and
      ``deleted=0`` — zero refs on a thread that HAS remaining
      checkpoints means ``channel_versions`` extraction is broken
      (schema drift / unexpected shape); the prune refuses to delete
      anything for that thread;
    * cap skip: ``skipped_reason=MAX_REFS_EXCEEDED``.

    Emitted at DEBUG (formerly INFO) — one line fires per
    (thread, checkpoint_ns) candidate the prune walks, which is many
    per maintenance sweep and pure noise at INFO. Operators wanting a
    single per-sweep summary line should grep
    ``op=blob_prune_summary`` instead; the maintenance module emits
    exactly one summary line per sweep.

    Suppressed when ``CHECKPOINT_PERF_LOGS`` is falsy, matching the other
    C4 emitters.
    """
    if not _logs_enabled():
        return
    line = (
        f"[CheckpointPerf] op=blob_prune "
        f"thread={thread_id[:8] if thread_id else '?'} "
        f"dry_run={1 if dry_run else 0} deleted={deleted} "
        f"bytes={bytes_freed} refs_seen={refs_seen} "
        f"observed_blob_count={observed_blob_count}"
    )
    if skipped_reason:
        line += f" skipped_reason={skipped_reason}"
    logger.debug(line)
