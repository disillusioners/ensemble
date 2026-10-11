"""Reference-aware ``checkpoint_blobs`` prune — Phase 1 C3 (PR4, LD-D1 Rev 2).

Single module owning the blob-prune algorithm + the fail-safe + the
per-pair destruction loop. The destructive SQL itself lives in
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

3. **Destructive-by-default** — the anti-join DELETE is the unconditional
   destructive path. No env-var, config-flag, or on/off switch disables
   it. The auto cycle (operation E of the maintenance timer) and the
   manual ``/execute`` endpoint both invoke it; only the manual preview
   ``/dry-run`` opts out via ``destructive=False`` (the kwarg reaches the
   SELECT-only ``count_blobs_anti_join`` arm with an explicit override;
   the ``if not destructive: ... continue`` guard stays in the
   destructive code path so the dry-run arm remains structurally
   unreachable to a DELETEs). This is structural, not merely
   conventional — ``tests/unit/services/
   test_maintenance_prune_direct_anti_join.py`` enforces the
   guard-If dominance at the AST level.

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
import time
from dataclasses import dataclass, field

from daemon.checkpoint_adapter import PostgresCheckpointerAdapter
from daemon.checkpoint_perf import log_blob_prune
from daemon.constants import (
    CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD,
)

logger = logging.getLogger(__name__)


@dataclass
class BlobPruneSummary:
    """One maintenance-cycle result of the blob prune.

    Carries dual-flavor keys [AM-11, A-2/A-5 RATIFIED]: the dry-run arm
    populates ``would_delete_count`` + ``would_free_bytes``; the destructive
    arm populates ``total_deleted`` + ``total_bytes_freed``. Downstream
    readers (FE / API) branch on :attr:`destructive` (== ``not dry_run``)
    to pick the active set — never reading the wrong one for the cycle
    kind. The ``summary`` flavor surfaced to clients is owned by
    :meth:`daemon.services.maintenance.CheckpointRunResult.to_summary_dict`
    so the wire shape stays in one place.
    """

    backend: str = "postgres"
    dry_run: bool = True
    scanned_pairs: int = 0
    total_deleted: int = 0
    total_bytes_freed: int = 0
    would_delete_count: int = 0   # [AM-11] dry-run arm accumulation (canonical name)
    would_free_bytes: int = 0     # [AM-11] dry-run arm accumulation (canonical name)
    skipped: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def destructive(self) -> bool:
        """True when this cycle actually executed DELETEs."""
        return not self.dry_run

    def to_summary_blobs_dict(self, *, skipped_cap: int = 1000) -> dict[str, Any]:
        """The FROZEN ``blobs`` block of a CycleSummary (plan-overview §2).

        Dual-flavor keys per [AM-11, A-5 RATIFIED]: ``destructive=False``
        emits the dry-run shape (would_* + symmetry keys); destructive=True
        emits the destructive shape (deleted/bytes_freed). FE branches on
        ``destructive`` first. ``skipped`` is serialized as a list of
        ``{thread_id, checkpoint_ns, reason}`` dicts (tuples are NOT
        JSON-safe in ``summary_json``) and is capped at ``skipped_cap``
        entries with ``skipped_truncated: true`` when the cap fires.
        """
        skipped_list = [
            {"thread_id": t, "checkpoint_ns": ns, "reason": r}
            for (t, ns, r) in self.skipped[:skipped_cap]
        ]
        truncated = len(self.skipped) > skipped_cap
        if self.destructive:
            return {
                "scanned_pairs": self.scanned_pairs,
                "deleted": self.total_deleted,
                "bytes_freed": self.total_bytes_freed,
                "destructive": True,
                "skipped": skipped_list,
                "skipped_truncated": truncated,
            }
        return {
            "scanned_pairs": self.scanned_pairs,
            "would_delete_count": self.would_delete_count,
            "would_free_bytes": self.would_free_bytes,
            "would_delete": self.would_delete_count,
            "bytes": self.would_free_bytes,
            "destructive": False,
            "skipped": skipped_list,
            "skipped_truncated": truncated,
        }


async def prune_unreferenced_blobs(
    checkpointer,
    *,
    max_refs_per_thread: int = CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD,
    destructive: bool = True,
) -> BlobPruneSummary:
    """Run one reference-aware blob-prune cycle over all (thread, ns) pairs.

    **Destructive by default.** The anti-join DELETE is the unconditional
    destructive path; the auto cycle and the manual ``/execute`` endpoint
    invoke it without an opt-in. Only the manual preview
    ``/dry-run`` opts out via ``destructive=False`` (the kwarg reaches the
    SELECT-only ``count_blobs_anti_join`` arm). The historical dual-arm
    env-flag gate (``CHECKPOINT_BLOB_PRUNE_DRY_RUN`` /
    ``CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE``) and the conservative ladder
    that gated it are gone — see feature/blob-prune-default-on.

    The ``destructive`` kwarg is the SOLE control surface on this
    function. ``True`` is the default for every caller that does not
    explicitly opt out; ``False`` forces the SELECT-only dry-run arm. The
    local name ``destructive`` is preserved (rebinds at the gate feed)
    so the AST dominance pin in
    ``tests/unit/services/test_maintenance_prune_direct_anti_join.py``
    keeps its ``ast.Name(id="destructive")`` predicate green — see R-9
    in the override work that established the pin.

    Per-pair destruction is bounded: the destructive arm invokes
    ``delete_blobs_anti_join`` (PG) which breaks the per-pair DELETE
    into chunks of at most
    ``CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE`` rows, each chunk a
    separate SERIALIZABLE transaction with the bounded retry wrap. No
    single statement is unbounded — pool-hazard class closed.

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
                    # T1.1 / [AM-11] canonical accumulation fields.
                    # Previously the dry-run arm computed and logged
                    # these but discarded the values — the contract
                    # wire shape needed them. Destructive arm below
                    # leaves these at 0.
                    summary.would_delete_count += would_delete
                    summary.would_free_bytes += bytes_would_free
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
        #
        # [AM-11] dry-run canonical fields are emitted alongside the
        # destructive fields — one line carries both, with dry-run
        # fields zeroed when destructive=True (and vice-versa).
        duration_ms = int((time.perf_counter() - sweep_t0) * 1000)
        logger.info(
            "[CheckpointPerf] op=blob_prune_summary threads=%d deleted=%d "
            "bytes=%d would_delete=%d would_free=%d dry_run=%d "
            "duration_ms=%d scanned_pairs=%d skipped=%d backend=%s",
            len(unique_threads),
            summary.total_deleted,
            summary.total_bytes_freed,
            summary.would_delete_count,
            summary.would_free_bytes,
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
