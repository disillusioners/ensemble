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

import asyncio
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.engine import Engine

from daemon.checkpoint_adapter import PostgresCheckpointerAdapter
from daemon.checkpoint_perf import log_blob_prune
from daemon.constants import (
    CHECKPOINT_BLOB_PRUNE_MAX_REFS_PER_THREAD,
    VACUUM_IDLE_IN_TXN_TIMEOUT_MS,
    VACUUM_LOCK_TIMEOUT_MS,
    VACUUM_STATEMENT_TIMEOUT_MS,
)
from daemon.services.vacuum_engine import create_vacuum_engine

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
    # W1 partial-completion signal (fix cycle 1): pairs whose DESTRUCTIVE
    # chunk loop stopped early — ``"retry_exhausted"`` (isolation-family
    # retry budget spent) or ``"over_batch_limit"`` (LIMIT-guard break).
    # Tuples mirror ``skipped``: (thread_id, checkpoint_ns, reason). The
    # deletes that DID happen stay counted in total_deleted /
    # total_bytes_freed; this list makes the incompleteness explicit so
    # a partially-pruned pair can no longer read as ordinary completion
    # in the ``maintenance_runs`` audit row. Dry-run sweeps never
    # populate it (the dry-run arm never deletes).
    partial_pairs: list[tuple[str, str, str]] = field(default_factory=list)
    partial_chunks_skipped: int = 0

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
            payload = {
                "scanned_pairs": self.scanned_pairs,
                "deleted": self.total_deleted,
                "bytes_freed": self.total_bytes_freed,
                "destructive": True,
                "skipped": skipped_list,
                "skipped_truncated": truncated,
            }
            # W1 partial-completion signal (fix cycle 1) — emitted ONLY
            # when at least one destructive pair stopped early, so the
            # frozen wire shape stays byte-identical on healthy runs
            # and the dry-run flavor (which never deletes) can never
            # carry a phantom partial block.
            if self.partial_pairs:
                payload["partial_completion"] = {
                    "stopped_early_pairs": len(self.partial_pairs),
                    "chunks_skipped": self.partial_chunks_skipped,
                    "pairs": [
                        {"thread_id": t, "checkpoint_ns": ns, "reason": r}
                        for (t, ns, r) in self.partial_pairs[:skipped_cap]
                    ],
                    "truncated": len(self.partial_pairs) > skipped_cap,
                }
            return payload
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
                # W1 (fix cycle 1): read the adapter's per-pair stop
                # state, published by the PG chunk loop
                # (``last_pair_stop_reason`` / ``last_pair_chunks_skipped``).
                # Duck-typed read with a str/int guard so mocks that
                # don't define the channel (MagicMock auto-attrs are
                # neither) read as neutral — no phantom partials.
                raw_reason = getattr(checkpointer, "last_pair_stop_reason", None)
                pair_stop = raw_reason if isinstance(raw_reason, str) else None
                raw_chunks = getattr(
                    checkpointer, "last_pair_chunks_skipped", 0
                )
                pair_chunks_skipped = (
                    raw_chunks
                    if isinstance(raw_chunks, int)
                    and not isinstance(raw_chunks, bool)
                    else 0
                )
                if pair_stop is not None:
                    # Early-stopped pair: partial totals above are REAL
                    # (those deletes committed) but the pair is NOT
                    # exhausted — surface it so the audit row cannot
                    # read as ordinary completion.
                    summary.partial_pairs.append(
                        (thread_id, checkpoint_ns, pair_stop)
                    )
                    summary.partial_chunks_skipped += pair_chunks_skipped
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
                    "(deleted=%d bytes=%d%s)",
                    duration_ms,
                    deleted,
                    bytes_freed,
                    (
                        f" stopped_early={pair_stop} "
                        f"chunks_skipped={pair_chunks_skipped}"
                        if pair_stop is not None
                        else ""
                    ),
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
            "duration_ms=%d scanned_pairs=%d skipped=%d "
            "stopped_early=%d backend=%s",
            len(unique_threads),
            summary.total_deleted,
            summary.total_bytes_freed,
            summary.would_delete_count,
            summary.would_free_bytes,
            1 if summary.dry_run else 0,
            duration_ms,
            summary.scanned_pairs,
            len(summary.skipped),
            len(summary.partial_pairs),
            summary.backend,
        )
        return summary
    except Exception as e:  # noqa: BLE001
        # Outer catch: even candidate enumeration failing must not raise
        # into the maintenance loop (blob-prune failure must NEVER break
        # the retention prune that already ran, or the next cycle's ops).
        logger.error("Reference-aware blob prune failed: %s", e)
        return summary


# ── Post-prune statistics refresh (ANALYZE) ─────────────────────────────────
#
# WHY: a destructive prune DELETEs rows from the three checkpoint tables.
# PG's planner statistics (``pg_statistic``) then describe a table that no
# longer exists; until the next autovacuum/ANALYZE lands, the planner is
# working from a row-count/selectivity picture that no longer matches the
# table. The read-path audit measured that class of plan regression on the
# pinned latest-checkpoint SELECT (the 8.56 ms-vs-0.064 ms gap). HONESTY:
# the 8.56 ms state was NOT reproducible at audit time (autovacuum had
# already analyzed; the live plan was an index scan) — the pathology is
# EPISODIC, so this ANALYZE is PREVENTION, not a repair of an
# always-on regression. The companion half of the same plan (psycopg3
# ``prepare_threshold=None``, so the server never serves a cached generic
# plan) lives at the saver-pool construction site in
# ``daemon/persistence.py``.
#
# WHERE: the DEDICATED per-cycle autocommit engine — the SAME shape the
# VACUUM lane uses (``daemon/services/vacuum_engine.py``: a NullPool engine
# derived from the daemon's shared engine, ``autocommit=True``, the daemon's
# UTC session frame, bounded statement/lock/idle timeouts). Deliberately
# NOT the hot psycopg saver pool (the checkpoint read path) and deliberately
# NOT the adapter's shared asyncpg pool — that pool also serves the read
# path, and borrowing a connection from it for an ANALYZE would compete
# with the very reads this lane protects.
#
# WHEN: ONLY when the run ACTUALLY deleted rows. The steady state of the
# sweep is a no-op (every (thread, ns) pair already inside its retention
# cap); a no-op sweep must not pay for an ANALYZE.
#
# FAILURE: log-and-continue, per table. An ANALYZE failure must never fail
# the prune that preceded it.

#: The three checkpoint tables whose planner statistics are refreshed after
#: a destructive prune. Same order as the VACUUM FULL lane's table order
#: (``daemon/services/maintenance.py::_VACUUM_FULL_TABLES``).
ANALYZE_TARGET_TABLES: tuple[str, ...] = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)


@dataclass
class AnalyzeOutcome:
    """Outcome of one post-prune ANALYZE sweep.

    ``ran`` is True only when the sweep actually reached the server (an
    engine was built and the tables were visited). ``skipped_reason``
    explains a non-run: ``no_rows_deleted`` (the steady-state no-op
    sweep), ``no_shared_engine`` / ``not_postgresql`` (nothing to run
    against), ``engine_build_failed``.

    ``deleted_rows`` and ``deleted_threads`` are SEPARATE units (R-e):
    rows are rows (checkpoints + writes + blobs), threads are threads
    (Op A's orphaned thread sweep). They are never summed into one
    number.
    """

    ran: bool = False
    skipped_reason: str | None = None
    deleted_rows: int = 0
    deleted_threads: int = 0
    analyzed_tables: list[str] = field(default_factory=list)
    failed_tables: list[str] = field(default_factory=list)
    duration_ms: int = 0


def _analyze_one_table(engine: Engine, table_name: str) -> None:
    """Run ``ANALYZE <table>`` on the dedicated autocommit engine.

    Synchronous — the caller wraps it in ``asyncio.to_thread`` so the
    event loop is never blocked (ANALYZE samples rows and can hold its
    lock for a while on a large table). The table name comes from the
    module-level :data:`ANALYZE_TARGET_TABLES` allow-list: PG has no
    parameter binding for identifiers, so the interpolation is safe by
    construction — no caller-supplied string ever reaches the SQL.
    """
    with engine.connect() as conn:
        conn.execute(text(f"ANALYZE {table_name}"))


async def analyze_checkpoint_tables_after_prune(
    shared_engine: Engine | None,
    *,
    deleted_rows: int,
    deleted_threads: int = 0,
    statement_ms: int = VACUUM_STATEMENT_TIMEOUT_MS,
    lock_ms: int = VACUUM_LOCK_TIMEOUT_MS,
    idle_ms: int = VACUUM_IDLE_IN_TXN_TIMEOUT_MS,
) -> AnalyzeOutcome:
    """Refresh planner statistics on the three checkpoint tables.

    Call this at the END of a destructive prune path with the number of
    rows the run actually deleted. The ANALYZE fires ONLY when
    ``deleted_rows > 0`` — a no-op sweep returns immediately without
    building an engine or touching the DB.

    Never raises: engine-build failures and per-table ANALYZE failures
    are logged and swallowed (an ANALYZE failure must never fail the
    prune that preceded it).

    Args:
        shared_engine: the daemon's shared SQLAlchemy engine — only its
            URL/dialect are read (the sweep runs on its OWN per-cycle
            engine). ``None`` ⇒ the sweep is skipped with a WARNING.
        deleted_rows: rows actually deleted by this run (checkpoints +
            writes + blobs). ``0`` ⇒ skip, UNLESS ``deleted_threads`` is
            non-zero (see below).
        deleted_threads: THREADS removed by the orphan sweep — a
            DIFFERENT unit from rows (R-e: the two are never summed into
            one number). An orphaned thread always removes at least one
            ``checkpoints`` row, so a non-zero thread count is a
            lower-bound proof that SOMETHING was deleted ⇒ the gate
            fires on it too.
        statement_ms / lock_ms / idle_ms: per-statement timeouts for the
            dedicated engine (defaults = the plain-VACUUM(ANALYZE) lane's
            magnitudes).

    Returns:
        An :class:`AnalyzeOutcome` describing what ran.
    """
    outcome = AnalyzeOutcome(
        deleted_rows=max(0, int(deleted_rows or 0)),
        deleted_threads=max(0, int(deleted_threads or 0)),
    )

    # ── Gate 1: did this run actually delete anything? ────────────────────
    if outcome.deleted_rows <= 0 and outcome.deleted_threads <= 0:
        outcome.skipped_reason = "no_rows_deleted"
        logger.debug(
            "[CheckpointPerf] post-prune ANALYZE skipped: no rows "
            "deleted in this run (deleted_rows=0)"
        )
        return outcome

    # ── Gate 2: is there a PostgreSQL engine to run against? ──────────────
    if shared_engine is None:
        outcome.skipped_reason = "no_shared_engine"
        logger.warning(
            "[CheckpointPerf] post-prune ANALYZE skipped: no shared "
            "engine wired — planner statistics stay stale for this "
            "cycle (deleted_rows=%d)",
            outcome.deleted_rows,
        )
        return outcome
    if shared_engine.dialect.name != "postgresql":
        outcome.skipped_reason = "not_postgresql"
        logger.warning(
            "[CheckpointPerf] post-prune ANALYZE skipped: shared engine "
            "dialect is %r, not postgresql — planner statistics stay "
            "stale for this cycle (deleted_rows=%d)",
            shared_engine.dialect.name,
            outcome.deleted_rows,
        )
        return outcome

    # ── Build the dedicated per-cycle autocommit engine ───────────────────
    try:
        engine = create_vacuum_engine(
            shared_engine,
            statement_ms=statement_ms,
            lock_ms=lock_ms,
            idle_ms=idle_ms,
        )
    except Exception as exc:  # noqa: BLE001 — never fatal to the prune
        outcome.skipped_reason = "engine_build_failed"
        logger.warning(
            "[CheckpointPerf] post-prune ANALYZE skipped: dedicated "
            "engine build failed (%s: %s) — planner statistics stay "
            "stale for this cycle (never fatal to the prune)",
            type(exc).__name__,
            exc,
        )
        return outcome

    outcome.ran = True
    t0 = time.perf_counter()
    try:
        for table in ANALYZE_TARGET_TABLES:
            try:
                await asyncio.to_thread(_analyze_one_table, engine, table)
                outcome.analyzed_tables.append(table)
            except Exception as exc:  # noqa: BLE001 — per-table isolation
                outcome.failed_tables.append(table)
                logger.warning(
                    "[CheckpointPerf] post-prune ANALYZE failed for table "
                    "%s (%s: %s) — continuing (statistics refresh is "
                    "best-effort; never fatal to the prune)",
                    table,
                    type(exc).__name__,
                    exc,
                )
    finally:
        # Per-cycle engine — ALWAYS disposed (NullPool + dispose in
        # ``finally``), exactly like the VACUUM lane's engines.
        try:
            engine.dispose()
        except Exception:  # noqa: BLE001 — disposal is best-effort
            logger.debug(
                "[CheckpointPerf] post-prune ANALYZE engine dispose "
                "failed (non-fatal)",
                exc_info=True,
            )
    outcome.duration_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "[CheckpointPerf] op=post_prune_analyze deleted_rows=%d "
        "deleted_threads=%d analyzed=%d failed=%d duration_ms=%d",
        outcome.deleted_rows,
        outcome.deleted_threads,
        len(outcome.analyzed_tables),
        len(outcome.failed_tables),
        outcome.duration_ms,
    )
    return outcome


# ── Blob byte census (plan Track-1 item 4) ───────────────────────────────────
#
# WHY: the read-amplification audit measured where the checkpoint
# storage mass actually lives (the ``messages`` channel carried ~99.6 %
# of the bytes) — a LIVE measurement, not an inference. The census
# makes that measurement REPEATABLE: one cheap aggregate per
# maintenance cycle, landing in ``maintenance_runs.summary_json`` (the
# reliable channel — the maintenance cycle's own skip line WAS DEBUG,
# so a grep-based audit was a FALSE-NEGATIVE channel; that line is
# INFO since plan Track-1 item 8, but the audit row stays the
# authoritative channel).
#
# WHY ``pg_column_size`` AND NOT ``octet_length``: ``pg_column_size``
# reports the STORED size of the datum (compressed / TOAST-external
# sizes included) WITHOUT detoasting the value, so the aggregate stays
# a heap scan on a multi-GB table. ``octet_length(blob)`` would fetch
# every TOASTed value — a full read of the table's payload bytes on
# each cycle. The number is therefore the STORED size, not the logical
# payload size; the difference is the compression/TOAST overhead.
#
# WHERE: the DEDICATED per-cycle autocommit engine (the same shape the
# post-prune ANALYZE lane above uses) — NEVER the hot psycopg saver
# pool and NEVER the adapter's shared asyncpg pool (both serve the
# checkpoint read path this census is meant to protect).
#
# FAILURE: never raises. A census that cannot be taken is reported as
# ``ran=False`` + a ``skipped_reason`` — the cycle that preceded it
# must never fail because of an observation.


#: The aggregate — one row per blob channel.
_BLOB_BYTES_BY_CHANNEL_SQL = (
    "SELECT channel, COUNT(*) AS rows, "
    "COALESCE(SUM(pg_column_size(blob)), 0) AS bytes "
    "FROM checkpoint_blobs "
    "GROUP BY channel "
    "ORDER BY bytes DESC"
)


@dataclass
class BlobBytesCensus:
    """The per-channel blob byte census of one maintenance cycle.

    ``ran`` is True only when the aggregate actually reached the server.
    ``skipped_reason`` explains a non-run: ``no_shared_engine`` /
    ``not_postgresql`` (nothing to run against) or
    ``engine_build_failed`` / ``query_failed`` (the observation failed
    — the cycle that preceded it is unaffected).
    """

    ran: bool = False
    skipped_reason: str | None = None
    rows: int = 0
    total_bytes: int = 0
    by_channel: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0


def _collect_blob_bytes_sync(engine: Engine) -> tuple[int, int, dict[str, int]]:
    """Run the per-channel aggregate. Returns ``(rows, bytes, by_channel)``."""
    with engine.connect() as conn:
        rows = conn.execute(text(_BLOB_BYTES_BY_CHANNEL_SQL)).fetchall()
    by_channel: dict[str, int] = {}
    total = 0
    count = 0
    for row in rows:
        channel = row[0]
        channel_rows = int(row[1] or 0)
        channel_bytes = int(row[2] or 0)
        by_channel[str(channel)] = channel_bytes
        total += channel_bytes
        count += channel_rows
    return count, total, by_channel


async def collect_blob_bytes_by_channel(
    shared_engine: Engine | None,
    *,
    statement_ms: int = VACUUM_STATEMENT_TIMEOUT_MS,
    lock_ms: int = VACUUM_LOCK_TIMEOUT_MS,
    idle_ms: int = VACUUM_IDLE_IN_TXN_TIMEOUT_MS,
) -> BlobBytesCensus:
    """Measure the checkpoint blob storage mass per channel.

    Never raises: a failed census is reported as ``ran=False`` plus a
    ``skipped_reason``.

    Args:
        shared_engine: the daemon's shared SQLAlchemy engine — only its
            URL/dialect are read (the census runs on its OWN per-cycle
            engine). ``None`` ⇒ skipped with ``no_shared_engine``.
        statement_ms / lock_ms / idle_ms: per-statement timeouts for the
            dedicated engine (the VACUUM lane's magnitudes).

    Returns:
        A :class:`BlobBytesCensus`.
    """
    outcome = BlobBytesCensus()
    if shared_engine is None:
        outcome.skipped_reason = "no_shared_engine"
        logger.warning(
            "[CheckpointPerf] blob byte census skipped: no shared engine "
            "wired — the per-channel storage breakdown is unavailable "
            "this cycle"
        )
        return outcome
    if shared_engine.dialect.name != "postgresql":
        outcome.skipped_reason = "not_postgresql"
        logger.warning(
            "[CheckpointPerf] blob byte census skipped: shared engine "
            "dialect is %r, not postgresql — the per-channel storage "
            "breakdown is unavailable this cycle",
            shared_engine.dialect.name,
        )
        return outcome
    try:
        engine = create_vacuum_engine(
            shared_engine,
            statement_ms=statement_ms,
            lock_ms=lock_ms,
            idle_ms=idle_ms,
        )
    except Exception as exc:  # noqa: BLE001 — never fatal to the cycle
        outcome.skipped_reason = "engine_build_failed"
        logger.warning(
            "[CheckpointPerf] blob byte census skipped: dedicated engine "
            "build failed (%s: %s) — the per-channel storage breakdown is "
            "unavailable this cycle",
            type(exc).__name__,
            exc,
        )
        return outcome

    t0 = time.perf_counter()
    try:
        rows, total, by_channel = await asyncio.to_thread(
            _collect_blob_bytes_sync, engine
        )
        outcome.ran = True
        outcome.rows = rows
        outcome.total_bytes = total
        outcome.by_channel = by_channel
    except Exception as exc:  # noqa: BLE001 — never fatal to the cycle
        outcome.skipped_reason = "query_failed"
        logger.warning(
            "[CheckpointPerf] blob byte census failed (%s: %s) — the "
            "per-channel storage breakdown is unavailable this cycle "
            "(the cleanup cycle itself is unaffected)",
            type(exc).__name__,
            exc,
        )
    finally:
        try:
            engine.dispose()
        except Exception:  # noqa: BLE001 — disposal is best-effort
            logger.debug(
                "[CheckpointPerf] blob byte census engine dispose failed "
                "(non-fatal)",
                exc_info=True,
            )
    outcome.duration_ms = int((time.perf_counter() - t0) * 1000)
    return outcome
