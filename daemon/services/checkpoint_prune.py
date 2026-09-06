"""Reference-aware ``checkpoint_blobs`` prune — Phase 1 C3 (PR4, LD-D1 Rev 2).

Single module owning the blob-prune algorithm + the fail-safe. The
destructive SQL itself lives in
``daemon/checkpoint_adapter.py::PostgresCheckpointerAdapter`` (the
``count_blobs_anti_join`` / ``delete_blobs_anti_join`` pair sharing one
``_BLOB_ANTI_JOIN_PREDICATE``); this module decides WHO gets pruned and
WHETHER deletion is allowed at all.

Design invariants (phase1-plan.md §C3, Hard Constraint 6):

1. **No naive DELETE** — blobs are versioned and shared across checkpoint
   reconstructions. A blob dies only when its (channel, version) is not
   referenced by ``checkpoint->'channel_versions'`` of ANY REMAINING
   checkpoint row in the SAME (thread_id, checkpoint_ns) — the exact
   complement of the upstream saver's reader join (ns-matched on both
   sides).

2. **Fail-safe zero-refs** — if the reference extraction yields 0 entries
   for a (thread, ns) pair that HAS remaining checkpoints, the extraction
   itself is presumed broken (schema drift / unexpected shape): the pair
   is SKIPPED entirely with an ERROR log and zero rows deleted. Detection
   (the ERROR log) and prevention (the ``continue`` before any counting /
   deleting call) are separate, individually tested behaviors.

3. **Conservative ladder** — the operation is DRY-RUN by default. The
   destructive arm is only reachable when
   ``blob_prune_destructive_enabled()`` holds at call time: BOTH
   ``CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1`` AND
   ``CHECKPOINT_BLOB_PRUNE_DRY_RUN=0``. With the gate off, the only arm
   the loop can reach is ``count_blobs_anti_join`` (a SELECT); the
   ``delete_blobs_anti_join`` call site sits textually AFTER the
   ``if not destructive: ... continue`` guard, so no call path reaches a
   DELETE. This is structural, not merely conventional —
   ``tests/unit/services/test_maintenance_prune_direct_anti_join.py``
   enforces it at the AST level.

4. **Failure isolation** — a per-pair exception is logged and skipped;
   the prune never raises into the maintenance loop, and a failure here
   can never break the 50-cap retention prune (Operation D runs to
   completion BEFORE this operation is invoked, and this function has no
   raising path).

5. **PostgreSQL-only** — SQLite has no ``checkpoint_blobs`` table; the
   operation short-circuits with a WARNING (once per invocation) before
   touching any adapter blob method.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

from daemon.checkpoint_adapter import PostgresCheckpointerAdapter
from daemon.checkpoint_perf import log_blob_prune
from daemon.constants import (
    CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE,
    CHECKPOINT_BLOB_PRUNE_DRY_RUN,
    CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD,
)

logger = logging.getLogger(__name__)

# Env-flag names (the conservative ladder's only configuration surface —
# this is config, NOT a public API).
ENV_BLOB_PRUNE_DRY_RUN = "CHECKPOINT_BLOB_PRUNE_DRY_RUN"
ENV_BLOB_PRUNE_DESTRUCTIVE = "CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE"


def blob_prune_destructive_enabled() -> bool:
    """True ONLY when BOTH env flags arm the destructive path.

    ``CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1`` AND
    ``CHECKPOINT_BLOB_PRUNE_DRY_RUN=0`` — evaluated at CALL time (not
    import time) so tests and operators can flip the ladder without
    reloading the daemon. Defaults come from ``daemon.constants``
    (destructive OFF, dry-run ON).
    """
    dry_run_default = "1" if CHECKPOINT_BLOB_PRUNE_DRY_RUN else "0"
    destructive_default = "1" if CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE else "0"
    dry_run = os.environ.get(ENV_BLOB_PRUNE_DRY_RUN, dry_run_default)
    destructive = os.environ.get(ENV_BLOB_PRUNE_DESTRUCTIVE, destructive_default)
    return destructive.strip() == "1" and dry_run.strip() == "0"


@dataclass
class BlobPruneSummary:
    """One maintenance-cycle result of the blob prune."""

    backend: str = "postgres"
    dry_run: bool = True
    scanned_pairs: int = 0
    total_deleted: int = 0
    total_bytes_freed: int = 0
    skipped: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def destructive(self) -> bool:
        """True when this cycle actually executed DELETEs."""
        return not self.dry_run


async def prune_unreferenced_blobs(
    checkpointer,
    *,
    max_refs_per_thread: int = CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD,
) -> BlobPruneSummary:
    """Run one reference-aware blob-prune cycle over all (thread, ns) pairs.

    Dry-run by default (reports would-delete counts + bytes, deletes
    nothing). Destructive only when
    :func:`blob_prune_destructive_enabled` holds — see the module
    docstring for the structural-gate argument.

    Never raises: per-pair failures are logged and skipped; the outer
    try/except makes even candidate-enumeration failures non-fatal (the
    maintenance loop's other operations are unaffected).
    """
    summary = BlobPruneSummary()
    try:
        # PostgreSQL-only: the SQLite saver has no checkpoint_blobs table.
        if not isinstance(checkpointer, PostgresCheckpointerAdapter):
            logger.warning(
                "[CheckpointPerf] blob_prune skipped: backend is not "
                "PostgreSQL — SQLite has no checkpoint_blobs table "
                "(PostgreSQL-only operation)"
            )
            summary.backend = "sqlite"
            return summary

        destructive = blob_prune_destructive_enabled()
        summary.dry_run = not destructive

        # D21: ALL (thread_id, checkpoint_ns) pairs — find_excess_checkpoint_
        # groups' HAVING clause would silently skip single-checkpoint
        # threads whose blobs still need pruning.
        candidate_pairs = await checkpointer.find_all_thread_ns_pairs()
        if not candidate_pairs:
            logger.debug("No threads with checkpoints found for blob prune scan")
            return summary

        summary.scanned_pairs = len(candidate_pairs)

        # Sweep-level accounting for the one-shot INFO summary line emitted
        # at the end of the loop. Per-(thread, ns) observations go through
        # ``log_blob_prune`` at DEBUG — the per-pair INFO line that used to
        # fire (one per pair, often with ``deleted=0``) was pure noise.
        sweep_t0 = time.perf_counter()
        unique_threads: set[str] = set()

        for thread_id, checkpoint_ns, _ckpt_count in candidate_pairs:
            try:
                t0 = time.perf_counter()
                # Sweep-level accounting (summary emit below).
                unique_threads.add(thread_id)

                # ── Fail-safe pre-check (detection AND prevention) ──────
                # 0 refs on a pair that HAS remaining checkpoints (every
                # candidate comes FROM the checkpoints table) means the
                # channel_versions extraction is broken — schema drift or
                # an unexpected shape. Deleting on that signal would nuke
                # every blob of the pair. SKIP entirely.
                refs_seen = await checkpointer.count_refs_for_blob_thread(
                    thread_id, checkpoint_ns
                )
                if refs_seen <= 0:
                    # DETECTION — loud, unsuppressed-by-perf-gate ERROR.
                    logger.error(
                        "[CheckpointPerf] blob_prune ZERO_REFS_FAIL_SAFE "
                        "thread=%s ns=%s — channel_versions extraction "
                        "yielded 0 refs while checkpoints remain (possible "
                        "schema drift); skipping pair, zero rows deleted",
                        thread_id[:8],
                        checkpoint_ns,
                    )
                    # PREVENTION — continue before any count/delete call.
                    log_blob_prune(
                        thread_id,
                        dry_run=not destructive,
                        deleted=0,
                        refs_seen=0,
                        skipped_reason="ZERO_REFS_FAIL_SAFE",
                    )
                    summary.skipped.append(
                        (thread_id, checkpoint_ns, "ZERO_REFS_FAIL_SAFE")
                    )
                    continue

                if refs_seen > max_refs_per_thread:
                    log_blob_prune(
                        thread_id,
                        dry_run=not destructive,
                        deleted=0,
                        refs_seen=refs_seen,
                        skipped_reason="MAX_REFS_EXCEEDED",
                    )
                    summary.skipped.append(
                        (thread_id, checkpoint_ns, "MAX_REFS_EXCEEDED")
                    )
                    continue

                if not destructive:
                    # ── Dry-run arm — SELECT only, deletes nothing. ────
                    would_delete, bytes_would_free = (
                        await checkpointer.count_blobs_anti_join(
                            thread_id, checkpoint_ns
                        )
                    )
                    log_blob_prune(
                        thread_id,
                        dry_run=True,
                        deleted=would_delete,
                        refs_seen=refs_seen,
                        bytes_freed=bytes_would_free,
                    )
                    continue

                # ── Destructive arm ────────────────────────────────────
                # Reachable ONLY past the `if not destructive: ... continue`
                # guard above — i.e. only when blob_prune_destructive_
                # enabled() held at cycle start. (Structural gate; enforced
                # by an AST test over this module's source.)
                deleted, bytes_freed = await checkpointer.delete_blobs_anti_join(
                    thread_id, checkpoint_ns
                )
                duration_ms = int((time.perf_counter() - t0) * 1000)
                log_blob_prune(
                    thread_id,
                    dry_run=False,
                    deleted=deleted,
                    refs_seen=refs_seen,
                    bytes_freed=bytes_freed,
                )
                summary.total_deleted += deleted
                summary.total_bytes_freed += bytes_freed
                logger.debug(
                    "[CheckpointPerf] blob_prune pair done in %dms "
                    "(deleted=%d bytes=%d)",
                    duration_ms,
                    deleted,
                    bytes_freed,
                )
            except Exception as inner_exc:  # noqa: BLE001
                # Per-pair failure never breaks the cycle (risk table:
                # "Long-running prune blocks maintenance loop").
                logger.warning(
                    "[CheckpointPerf] blob_prune thread=%s ns=%s skipped, "
                    "error=%s: %s",
                    thread_id[:8],
                    checkpoint_ns,
                    type(inner_exc).__name__,
                    inner_exc,
                )
                summary.skipped.append(
                    (thread_id, checkpoint_ns, f"ERROR:{type(inner_exc).__name__}")
                )
                continue

        # ONE INFO summary line per sweep — replaces the per-pair INFO
        # line that used to fire (one per (thread, ns) candidate, often
        # with ``deleted=0``, pure noise at INFO). Per-pair detail is
        # still available at DEBUG via ``log_blob_prune`` (emits
        # ``op=blob_prune thread=…``); operators wanting per-pair
        # diagnostics enable ``CHECKPOINT_PERF_LOGS=1`` and tail DEBUG.
        duration_ms = int((time.perf_counter() - sweep_t0) * 1000)
        logger.info(
            "[CheckpointPerf] op=blob_prune_summary threads=%d deleted=%d "
            "bytes=%d dry_run=%d duration_ms=%d scanned_pairs=%d "
            "skipped=%d backend=%s",
            len(unique_threads),
            summary.total_deleted,
            summary.total_bytes_freed,
            1 if summary.dry_run else 0,
            duration_ms,
            summary.scanned_pairs,
            len(summary.skipped),
            summary.backend,
        )
        return summary
    except Exception as e:  # noqa: BLE001
        # Outer catch: even candidate enumeration failing must not raise
        # into the maintenance loop (blob-prune failure must NEVER break
        # the retention prune that already ran, or the next cycle's ops).
        logger.error("Reference-aware blob prune failed: %s", e)
        return summary
