"""Checkpointer adapter abstraction for SQLite and PostgreSQL checkpoint databases.

This module provides a unified interface for checkpoint database operations,
abstracting away the differences between AsyncSqliteSaver and AsyncPostgresSaver.

Why this adapter exists:
- AsyncSqliteSaver exposes `.conn` and `.lock` for direct SQL access, but
  AsyncPostgresSaver does not have these attributes.
- maintenance.py (and future code) needs to perform raw SQL queries for
  checkpoint cleanup operations.
- This adapter wraps the database-specific details, allowing callers to use
  the same interface regardless of the backend.

Adapter pattern:
- CheckpointerAdapter (ABC): Defines the interface with 6 abstract methods
- SqliteCheckpointerAdapter: Wraps AsyncSqliteSaver, uses its .conn and .lock
- PostgresCheckpointerAdapter: Wraps AsyncPostgresSaver, uses asyncpg pool

The raw_saver property provides access to the underlying saver for LangGraph
operations (aget, aput, etc.) that are not covered by this adapter.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

try:
    # Incident 2026-10-10 review fix: the retry proxy subclasses
    # ``BaseCheckpointSaver`` so ``StateGraph.compile`` (via
    # ``ensure_valid_checkpointer``) accepts it as a valid saver.
    from langgraph.checkpoint.base import BaseCheckpointSaver
except ImportError:
    # Fenced fallback: any deployment where this submodule is
    # absent (older langgraph pin, packaging accident) loses the
    # ``isinstance`` gate but keeps the proxy functional. The
    # proxy still inherits from ``object`` so its surface is
    # identical to the language-level contract — only the
    # LangGraph compile-time gate would then raise TypeError.
    BaseCheckpointSaver = object  # type: ignore[assignment,misc]

from daemon.constants import (
    CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES,
    CHECKPOINT_SENTINEL_THREAD_ID,
)

logger = logging.getLogger(__name__)


# ── Incident 2026-10-10: narrow connection-failure retry wrapper ─────────
#
# The saver is now backed by a pool (see ``daemon.persistence``), so dead
# connections are normally replaced transparently. This wrapper is
# belt-and-braces for the narrow race window where a cursor-open lands
# on a connection that died AFTER ``check=`` validated it but BEFORE the
# statement ran (PG-side session kill mid-execution). It also covers
# admin-initiated terminations that propagate as
# ``psycopg.OperationalError: the connection is closed``.
#
# WHAT WE WRAP: the saver object (smaller surface than wrapping the
# adapter methods). LangGraph's reads/writes flow through the saver;
# adapter methods operate on the SEPARATE asyncpg pool, which has its
# own resilience (and is exercised by maintenance, not the hot instance
# path). Wrapping the saver covers exactly the failure class the
# incident produced, with one retry only.
#
# IDEMPOTENCY RATIONALE (writes): ``AsyncPostgresSaver`` upserts are
# keyed on (thread_id, checkpoint_id). A retry that re-runs an upsert
# after a cursor-open failure lands the same row in the same state —
# the previous attempt did not execute the statement, so there is no
# half-written state to overwrite. ``setup()`` is idempotent (DDL uses
# IF NOT EXISTS / CREATE INDEX IF NOT EXISTS) and is NEVER routed
# through this wrapper (we only wrap method calls, never setup). The
# READ path is naturally idempotent.
#
# SCOPE OF RETRY: SQLSTATE class 08 (connection exceptions:
# 08000-08999), 57P01 (admin_shutdown), 57P02 (crash_shutdown), 57P03
# (cannot_connect_now), and psycopg's ``OperationalError`` family
# carrying the "the connection is closed" message. Other errors
# propagate immediately. The class-08 catch covers asyncpg/psycopg
# exceptions that expose a ``.sqlstate`` attribute; the
# ``OperationalError`` catch covers the psycopg-3 idiomatic exception
# raised before SQLSTATE reaches the driver (cursor-open on a closed
# conn).
#
# On second failure we re-raise unchanged so the existing
# instance-state / error-classification pipeline (task_processor.py:651)
# sees the same exception class as before the fix — the wrapper is
# transparent to upstream consumers.
_RETRY_SQLSTATE_EXACT: frozenset[str] = frozenset(
    {"57P01", "57P02", "57P03"}
)
_RETRY_OPERATIONAL_CLOSED_SUBSTR: str = "the connection is closed"


def _is_retryable_connection_error(exc: BaseException) -> bool:
    """Classify an exception as a transient connection-class failure.

    Duck-typed on ``.sqlstate`` (psycopg and asyncpg both expose it)
    plus a fallback string match for psycopg's ``OperationalError: the
    connection is closed`` (which fires at cursor-open time, before
    SQLSTATE is available).

    The substring fallback is GUARDED by ``type(exc).__module__``
    starting with ``"psycopg"`` — psycopg's ``OperationalError``
    family lives in the ``psycopg`` / ``psycopg.errors`` /
    ``psycopg._adapters`` modules. Without this guard, any unrelated
    exception (e.g. an application-level ``RuntimeError`` whose
    message happens to contain the same English phrase) would be
    mis-classified as retryable and trigger a wasted retry.
    """
    sqlstate = getattr(exc, "sqlstate", None)
    if isinstance(sqlstate, str):
        if sqlstate in _RETRY_SQLSTATE_EXACT:
            return True
        if sqlstate[:2] == "08":
            return True
    # psycopg's OperationalError family — the most common surface for
    # "the connection is closed" when the cursor() open itself fails.
    # Guard: only fire the substring fallback when the exception's
    # module is a psycopg family module, so a coincidentally-worded
    # exception from elsewhere is not mis-classified.
    exc_module = type(exc).__module__ or ""
    if (
        exc_module.startswith("psycopg")
        and _RETRY_OPERATIONAL_CLOSED_SUBSTR in str(exc).lower()
    ):
        return True
    return False


def _wrap_saver_with_connection_retry(saver: Any) -> Any:
    """Wrap a saver with one-shot retry on connection-class failures.

    The wrapper forwards every attribute access and method call to the
    underlying saver. Every CONCRETE public member of the pinned
    ``BaseCheckpointSaver`` surface has an EXPLICIT forwarder (the MRO
    rule: base-concrete attributes resolve via MRO and ``__getattr__``
    never fires for them — see the proxy class docstring). Only the
    seven hot-path async ops carry the retry layer. Non-method access
    (e.g. ``wrapper.conn``, ``wrapper.lock``) passes through unchanged
    so existing consumers (the adapter's ``close()`` reads
    ``saver.conn``) are unaffected.

    Args:
        saver: An ``AsyncPostgresSaver`` (or any object with
            ``aget``/``aput``/``alist``/etc. methods).

    Returns:
        A proxy object that behaves like the saver but retries each
        method call once on a connection-class failure.
    """

    class _SaverRetryProxy(BaseCheckpointSaver):
        """One-shot retry proxy — see module docstring for rationale.

        Inherits from ``BaseCheckpointSaver`` so the LangGraph
        ``isinstance`` gate at ``ensure_valid_checkpointer``
        (``langgraph/types.py``) — and the companion gates at
        ``pregel/main.py`` — see this as a real saver.

        ── THE MRO RULE (round-2 root cause of K1/K2) ──────────────

        (a) Every attribute that is CONCRETE (non-abstract) on
        ``BaseCheckpointSaver`` resolves via normal MRO lookup, and
        ``__getattr__`` NEVER fires for it. ``__getattr__`` covers
        only attributes ABSENT from the base surface (``conn``,
        ``lock``, ``setup``, ...). In the pinned base
        (``langgraph/checkpoint/base/__init__.py``, langgraph 1.0.9 /
        langgraph-checkpoint 3.1.x) there are ZERO abstract methods —
        all 22 public methods plus the ``serde`` /
        ``config_specs`` surface members are concrete, including the
        ``get_next_version`` stub (raises for ``str`` versions) and
        the ``alist`` async-generator stub (raises
        NotImplementedError on first ``__anext__``). An
        ``__getattr__``-fall-through for any of them is INERT.

        (b) Therefore EVERY concrete public member of the base
        surface has an EXPLICIT forwarder below. The retry-wrapped
        set is the seven hot-path async ops (``aget``, ``aget_tuple``,
        ``aput``, ``aput_writes``, ``aget_delta_channel_history``,
        ``adelete_thread`` — plus ``aput``-family write coverage);
        everything else forwards with NO retry: ``get_next_version``
        is a synchronous ID mint (pregel wraps its own retry), and
        ``alist`` is an async generator (buffering the stream to
        retry it would change the memory profile; the pool's
        self-heal covers subsequent iteration attempts, and pregel's
        own retry-on-NextNotFound covers restart semantics).

        The sync twins (``get_tuple`` / ``list`` / ``put`` /
        ``put_writes`` / ``delete_thread`` / ``get`` / ``copy_thread``
        / ``delete_for_runs`` / ``prune`` /
        ``get_delta_channel_history``) are intentionally
        NotImplementedError in this daemon — the surface is
        async-only. The forwarders pass through to the wrapped
        saver's own sync stubs, so a stray sync caller gets the
        saver's own upstream-classified NotImplementedError, not a
        proxy artifact.

        (c) LOCKSTEP REQUIREMENT: the explicit-forwarders list below
        MUST be kept in lockstep with ``BaseCheckpointSaver``'s
        concrete public surface across langgraph bumps. A new
        concrete stub on the base that lacks a forwarder would
        silently shadow the wrapped saver via MRO.

        (d) ENFORCEMENT: the K-guard contract test
        (``tests/test_checkpoint_adapter_resilience.py::
        TestRealLanggraphSaverProxy::test_k_guard_concrete_surface_
        forwards_wrapped_overrides``) walks the REAL pinned
        ``BaseCheckpointSaver``, overrides each concrete public
        method on a fake wrapped saver, and asserts the proxy routes
        to the wrapped implementation — it fails loudly when a new
        base stub appears without a forwarder. Do not disable it.

        Why direct inheritance instead of ``register``:
        ``BaseCheckpointSaver`` in langgraph 1.0.9 is a plain
        ``type`` (``type(BaseCheckpointSaver) is type``), not
        ``ABCMeta``, so ``register`` is unavailable. Direct
        inheritance satisfies ``isinstance`` cleanly without any
        ``ABCMeta`` workaround, which would be a hidden compat
        hazard against a future langgraph bump that does flip
        ``BaseCheckpointSaver`` to ``ABCMeta``.
        """

        __slots__ = ("_saver",)

        def __init__(self, saver: Any) -> None:
            self._saver = saver

        def __getattr__(self, name: str) -> Any:
            # Only invoked when normal lookup misses — the explicit
            # forwarders below take priority, and every attribute
            # CONCRETE on BaseCheckpointSaver resolves via the MRO
            # before __getattr__ ever fires (the MRO rule — see the
            # class docstring). __getattr__ therefore only serves
            # attributes ABSENT from the base surface: ``conn``,
            # ``lock``, ``setup``, and future additions.
            return getattr(self._saver, name)

        async def _call_with_retry(self, method_name: str, args: tuple, kwargs: dict) -> Any:
            method = getattr(self._saver, method_name)
            try:
                return await method(*args, **kwargs)
            except Exception as exc:
                if not _is_retryable_connection_error(exc):
                    raise
                logger.warning(
                    "[CheckpointAdapter] %s raised %s (%s); retrying once "
                    "with a fresh pooled connection",
                    method_name,
                    type(exc).__name__,
                    str(exc)[:200],
                )
                # Second (and final) attempt. If this also fails, re-raise
                # unchanged so the upstream pipeline classifies the error
                # exactly as it did before the wrapper existed.
                return await method(*args, **kwargs)

        # ── Retry-wrapped async methods (hot checkpoint ops) ─────────
        # The seven methods below carry the one-shot connection-retry
        # layer. They are the LangGraph hot path (reads/writes during
        # pregel turns). Everything else on the base surface forwards
        # WITHOUT retry — see the class docstring for the rationale.
        async def aget(self, *args, **kwargs):
            return await self._call_with_retry("aget", args, kwargs)

        async def aget_tuple(self, *args, **kwargs):
            return await self._call_with_retry("aget_tuple", args, kwargs)

        async def aput(self, *args, **kwargs):
            return await self._call_with_retry("aput", args, kwargs)

        async def aput_writes(self, *args, **kwargs):
            return await self._call_with_retry("aput_writes", args, kwargs)

        async def aget_delta_channel_history(self, *args, **kwargs):
            return await self._call_with_retry(
                "aget_delta_channel_history", args, kwargs
            )

        async def adelete_thread(self, *args, **kwargs):
            # Public method on the AsyncPostgresSaver aio.py surface
            # (pinned aio.py:340 in langgraph-checkpoint-postgres
            # 3.1.x). Forwarded through the same retry wrapper as
            # the other public methods so a mid-cursor-open PG
            # failure during a thread drop is also recovered once.
            return await self._call_with_retry(
                "adelete_thread", args, kwargs
            )

        # NOTE: ``adelete`` is deliberately NOT defined here — it is
        # absent from BOTH the pinned langgraph-checkpoint-postgres
        # 3.1.0 aio.py surface AND the pinned BaseCheckpointSaver
        # (verified 2026-10-10). A stray ``proxy.adelete`` call
        # raises AttributeError via ``__getattr__``, which is the
        # honest surface. (A prior revision wrapped a non-existent
        # ``adelete`` in the retry layer — dead code, removed.)

        # ── Explicit forwarders — the MRO rule (K1/K2 cure) ──────────
        #
        # K1 — ``get_next_version``. CONCRETE on the pinned base
        # (raises NotImplementedError for ``str`` versions, which is
        # what every existing PG thread carries via
        # postgres/base.py ``f"{next_v:032}.{next_h:016}"``). Pregel
        # calls it at 8 sites (pregel/main.py:1538…2279) — without
        # this forwarder the FIRST message to any existing PG
        # instance post-deploy crashes in prepare_next_tasks.
        # NO retry: this is a synchronous ID mint, not a checkpoint
        # op; pregel's own retry around it covers transient failures.
        def get_next_version(self, current=None, channel=None):
            return self._saver.get_next_version(current, channel)

        # K2 — ``alist``. CONCRETE async-generator stub on the pinned
        # base (raises NotImplementedError on first ``__anext__``).
        # MRO resolves it before ``__getattr__``, so a
        # fall-through de-interception is INERT (the round-1 defect):
        # this explicit forwarder is the fix. It is a plain ``def``
        # returning the wrapped saver's async generator — the shape
        # LangGraph consumes via ``async for`` (pregel/main.py:1417).
        # NO retry: an async generator cannot be retried without
        # buffering the whole stream (memory-profile change on long
        # histories); the pool's self-heal covers subsequent
        # iteration attempts, and pregel's retry-on-NextNotFound
        # covers restart semantics. Zero live daemon callers of
        # alist/aget_state_history against the PG proxy (PR3
        # expected-0-live-alist-calls contract); LangGraph's own
        # alist walks are debug/admin paths, not hot.
        def alist(self, *args, **kwargs):
            return self._saver.alist(*args, **kwargs)

        # Remaining concrete async ops — forward (async-preserving),
        # no retry (not on the hot path; the retry set above is
        # deliberately the seven hot-path ops).
        async def acopy_thread(self, *args, **kwargs):
            return await self._saver.acopy_thread(*args, **kwargs)

        async def adelete_for_runs(self, *args, **kwargs):
            return await self._saver.adelete_for_runs(*args, **kwargs)

        async def aprune(self, *args, **kwargs):
            return await self._saver.aprune(*args, **kwargs)

        # Sync twins — concrete on the base, intentionally
        # NotImplementedError in this async-only daemon. The
        # forwarders route to the wrapped saver's own sync stubs so
        # a stray sync caller gets the saver's own
        # NotImplementedError (upstream-classified), not a proxy
        # artifact. NO retry (never invoked by the daemon).
        def get_tuple(self, *args, **kwargs):
            return self._saver.get_tuple(*args, **kwargs)

        def get(self, *args, **kwargs):
            return self._saver.get(*args, **kwargs)

        def list(self, *args, **kwargs):
            return self._saver.list(*args, **kwargs)

        def put(self, *args, **kwargs):
            return self._saver.put(*args, **kwargs)

        def put_writes(self, *args, **kwargs):
            return self._saver.put_writes(*args, **kwargs)

        def delete_thread(self, *args, **kwargs):
            return self._saver.delete_thread(*args, **kwargs)

        def delete_for_runs(self, *args, **kwargs):
            return self._saver.delete_for_runs(*args, **kwargs)

        def copy_thread(self, *args, **kwargs):
            return self._saver.copy_thread(*args, **kwargs)

        def prune(self, *args, **kwargs):
            return self._saver.prune(*args, **kwargs)

        def get_delta_channel_history(self, *args, **kwargs):
            return self._saver.get_delta_channel_history(*args, **kwargs)

        # ``with_allowlist`` — concrete on the base and SHADOWABLE:
        # the base implementation shallow-copies ``self`` and swaps
        # the serde. Without this forwarder, MRO would clone the
        # PROXY (dropping the wrapped saver's serde + allowlist and
        # returning a half-wired object). Forwarding returns the
        # wrapped saver's own clone; note the clone escapes retry
        # coverage — acceptable, it is a builder-time utility with
        # no pregel call sites on the checkpointer.
        def with_allowlist(self, *args, **kwargs):
            return self._saver.with_allowlist(*args, **kwargs)

        # Surface-member forwarders (same shadowing family as the
        # methods above): ``serde`` is a CLASS-attribute default on
        # the base (JsonPlusSerializer) that the wrapped saver
        # overrides per-instance in ``__init__``; ``config_specs``
        # is a base ``@property`` returning ``[]``. Without these,
        # MRO resolves the BASE's value on the proxy instead of the
        # wrapped saver's.
        @property
        def serde(self):
            return self._saver.serde

        @property
        def config_specs(self):
            return self._saver.config_specs

    return _SaverRetryProxy(saver)


# ── Phase 1 C3: reference-aware checkpoint_blobs prune (direct anti-join) ────────
#
# The shared NOT EXISTS predicate of the blob prune, used VERBATIM by both
# arms (the dry-run SELECT in ``count_blobs_anti_join`` and the destructive
# DELETE in ``delete_blobs_anti_join``) so the dry-run report can never
# diverge from what the destructive arm would delete.
#
# Correctness contract (mirrors the upstream AsyncPostgresSaver readers):
# the saver's own SELECT_SQL reconstructs ``channel_values`` by joining
# ``jsonb_each_text(checkpoint -> 'channel_versions')`` with
# ``checkpoint_blobs`` on (thread_id, checkpoint_ns, channel, version),
# and the delta-channel seed lookup reads blobs WHERE channel/version
# match a ``channel_versions`` entry of a chain row. Therefore a blob is
# reachable by ANY reader iff some REMAINING checkpoint row in the SAME
# (thread_id, checkpoint_ns) carries ``channel_versions[channel] ==
# version``. The anti-join below deletes exactly the complement of that
# reference relation.
#
# ns-matching on BOTH sides is THE critical predicate: blob rows and
# checkpoint rows are namespaced (subgraph checkpoints live in non-empty
# ``checkpoint_ns``); correlating ``c.checkpoint_ns = b.checkpoint_ns``
# (not a literal) means a blob can only be rescued by a checkpoint in its
# own namespace — matching the upstream readers — and a same-named
# (channel, version) pair in a DIFFERENT namespace can never mask an
# unreferenced blob (versions are per-namespace).
_BLOB_ANTI_JOIN_PREDICATE = """
      b.thread_id = $1
  AND b.checkpoint_ns = $2
  AND NOT EXISTS (
      SELECT 1
      FROM checkpoints c
      WHERE c.thread_id = b.thread_id
        AND c.checkpoint_ns = b.checkpoint_ns
        AND (c.checkpoint -> 'channel_versions' ->> b.channel) = b.version
  )"""


class CheckpointerAdapter(ABC):
    """Abstract base class for checkpoint database adapters.

    Provides a uniform interface for checkpoint cleanup operations,
    independent of the underlying database technology (SQLite or PostgreSQL).
    """

    @abstractmethod
    async def list_thread_ids(self) -> list[str]:
        """Return all distinct thread_ids from checkpoints table.

        Used by maintenance.py Operation A to find orphaned threads.
        """

    @abstractmethod
    async def get_checkpoint_ids(
        self, thread_id: str, checkpoint_ns: str, limit: int
    ) -> list[str]:
        """Get checkpoint_ids ordered newest-first, limited to `limit`.

        checkpoint_id is a UUID string where lexicographic ordering equals
        chronological ordering (newer UUIDs sort after older ones).

        Used by maintenance.py Operation D (_prune_thread_checkpoints)
        to determine which checkpoints to keep.
        """

    @abstractmethod
    async def delete_checkpoints_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """Delete checkpoints NOT in keep_ids. Returns deleted count.

        Used by maintenance.py Operation D to prune old checkpoints.
        """

    @abstractmethod
    async def delete_writes_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """Delete writes NOT in keep_ids. Returns deleted count.

        Used by maintenance.py Operation D to prune old writes
        corresponding to deleted checkpoints.
        """

    @abstractmethod
    async def count_writes_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """DRY-RUN arm of the Op D writes accounting — SELECT COUNT only.

        T2b / T1.5 — read-only mirror of :meth:`delete_writes_excluding`.
        Returns the count of write rows that WOULD be deleted if the
        destructive arm ran with the same ``keep_ids`` set, without
        actually issuing the DELETE. The Postgres and SQLite
        implementations mirror ``delete_writes_excluding`` exactly (same
        table name + same NOT-IN-set semantics) so the dry-run report
        can never disagree with the destructive arm's count for the
        same keep-set (the parity pin in
        ``tests/unit/services/test_checkpoint_prune_destructive_override.py``
        proves this).

        Used ONLY by the manual dry-run path
        (``CheckpointCleanupJob._compute_row_prune_dry_run`` — T1.5);
        the auto cycle never calls it (INV-1).
        """

    @abstractmethod
    async def adelete_thread(self, thread_id: str) -> None:
        """Delete all checkpoint data for a thread.

        Deletes from both checkpoints and writes tables.
        Used by maintenance.py Operations A, B, C for whole-thread deletion.
        """

    @abstractmethod
    async def find_excess_checkpoint_groups(
        self, max_per_thread: int
    ) -> list[tuple[str, str, int]]:
        """Find (thread_id, checkpoint_ns, count) groups exceeding max_per_thread.

        Used by maintenance.py Operation D to find threads with more
        checkpoints than the allowed maximum.

        Returns:
            List of (thread_id, checkpoint_ns, count) tuples where
            count > max_per_thread.
        """

    @abstractmethod
    async def find_all_thread_ns_pairs(self) -> list[tuple[str, str, int]]:
        """Return ALL (thread_id, checkpoint_ns, count) pairs — no HAVING filter.

        Phase 1 C3 (Rev 3 correction / decision D21): the blob-prune
        candidate enumeration must see EVERY (thread, ns) pair that has
        checkpoints, including single-checkpoint threads. The existing
        ``find_excess_checkpoint_groups`` cannot serve this role — its
        ``HAVING COUNT(*) > max_per_thread`` clause (and passing
        ``max_per_thread=1`` to it) would EXCLUDE threads with exactly 1
        checkpoint, whose blobs still need the reference-aware prune after
        retention deletes older rows.

        Returns:
            List of (thread_id, checkpoint_ns, count) tuples for every
            group present in ``checkpoints``, ordered for deterministic
            iteration.
        """

    @abstractmethod
    async def count_refs_for_blob_thread(
        self, thread_id: str, checkpoint_ns: str
    ) -> int:
        """Count distinct (channel, version) blob refs in remaining checkpoints.

        Phase 1 C3 fail-safe pre-check. Returns the number of DISTINCT
        (channel, version) pairs present in
        ``checkpoint -> 'channel_versions'`` across the REMAINING
        checkpoint rows of the given (thread_id, checkpoint_ns).

        Returns 0 when the thread has no remaining checkpoints OR when
        ``channel_versions`` is missing / not a JSON object / empty on
        every remaining row — the zero-refs signal the caller treats as
        "extraction broken: SKIP the thread entirely" (deleting on it
        would nuke every blob of the thread).
        """

    @abstractmethod
    async def count_blobs_anti_join(
        self, thread_id: str, checkpoint_ns: str
    ) -> tuple[int, int]:
        """DRY-RUN arm of the reference-aware blob prune — SELECT only.

        Returns ``(would_delete_count, bytes_would_free)`` for blobs of
        (thread_id, checkpoint_ns) whose (channel, version) is NOT
        referenced by ``channel_versions`` of ANY remaining checkpoint row
        in the SAME (thread_id, checkpoint_ns). This method contains no
        DELETE statement — it is the only arm the dry-run path can reach.

        SQLite: there is no ``checkpoint_blobs`` table for the SQLite
        saver (research-findings §3) — returns ``(0, 0)`` after logging a
        warning; blob prune is a PostgreSQL-only operation.
        """

    @abstractmethod
    async def count_blobs_referenced_only_by_excess(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> tuple[int, int]:
        """v3.2 projection helper — blobs referenced ONLY by excess rows.

        Returns ``(count, bytes)`` for blobs of (thread_id, checkpoint_ns)
        whose (channel, version) IS referenced by at least one row in
        the same (thread_id, checkpoint_ns) whose ``checkpoint_id`` is
        NOT in ``keep_ids`` AND NOT referenced by any row whose
        ``checkpoint_id`` IS in ``keep_ids``. This is the "referenced by
        excess only" reading of the Op-D projection (R-1 in the v3.2
        amendment) — the bytes that Op D of THIS pass will orphan for a
        follow-up blob prune run.

        Callers MUST pass a NON-empty ``keep_ids``: pairs whose keep
        set is empty (max_per_thread=0 edge) are SKIPPED upstream and
        contribute 0 to the projection (mirroring the destructive
        arm's skip) — they never reach this method. (With an empty
        ``keep_ids`` the SQL would degenerate to the pair's full
        referenced set, which is exactly why the empty case must stay
        caller-excluded.)

        SQLite: there is no ``checkpoint_blobs`` table — returns
        ``(0, 0)`` after logging a warning (mirror of
        :meth:`count_blobs_anti_join`).
        """

    @abstractmethod
    async def delete_blobs_anti_join(
        self, thread_id: str, checkpoint_ns: str
    ) -> tuple[int, int]:
        """DESTRUCTIVE arm of the reference-aware blob prune — DELETE only.

        Deletes blobs of (thread_id, checkpoint_ns) whose (channel,
        version) is NOT referenced by ``channel_versions`` of ANY
        remaining checkpoint row in the SAME (thread_id, checkpoint_ns),
        and returns ``(deleted_count, bytes_freed)``.

        The PostgreSQL implementation runs the DELETE inside an explicit
        SERIALIZABLE transaction with bounded retry on SQLSTATE 40001 /
        40P01 (budget: ``CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES``); on
        exhaustion it logs ERROR, skips the pair and returns ``(0, 0)``
        — isolation-family failures never raise into maintenance. The
        dry-run arm (``count_blobs_anti_join``) stays READ COMMITTED:
        a read-only report has no concurrency hazard to defend against.

        DANGER — data-destroying. The ONLY sanctioned call site is behind
        the structural gate ``blob_prune_destructive_enabled()`` in
        ``daemon/services/checkpoint_prune.py`` (requires BOTH
        ``CHECKPOINT_BLOB_PRUNE_DRY_RUN=0`` AND
        ``CHECKPOINT_BLOB_PRUNE_DESTRUCTIVE=1``). When that gate is off,
        no call path reaches this method.

        SQLite: no ``checkpoint_blobs`` table exists — returns ``(0, 0)``
        after logging a warning.
        """

    @property
    @abstractmethod
    def raw_saver(self) -> Any:
        """Access to the underlying saver for LangGraph operations.

        Returns the raw AsyncSqliteSaver or AsyncPostgresSaver instance.
        This is needed for checkpoint read/write operations like aget(),
        aput(), alist() that are not covered by this adapter.
        """

    @abstractmethod
    async def close(self) -> None:
        """Close the adapter and release any underlying resources.

        Concrete adapters must override this to release any long-lived
        connections/pools they own. Called from ``InstanceManager.shutdown()``
        during application shutdown to ensure connections are released
        cleanly. Failures should be logged and swallowed so the rest of
        the shutdown sequence can still run.
        """


class SqliteCheckpointerAdapter(CheckpointerAdapter):
    """Adapter wrapping AsyncSqliteSaver for checkpoint database operations.

    Uses the saver's internal .conn and .lock for thread-safe access,
    matching the existing pattern in maintenance.py.
    """

    def __init__(self, saver: Any) -> None:
        """Initialize the SQLite checkpointer adapter.

        Args:
            saver: An AsyncSqliteSaver instance.
        """
        self._saver = saver

    @property
    def raw_saver(self) -> Any:
        """Return the underlying AsyncSqliteSaver."""
        return self._saver

    async def list_thread_ids(self) -> list[str]:
        """Return all distinct thread_ids from checkpoints table."""
        async with self._saver.lock:
            cursor = await self._saver.conn.execute(
                "SELECT DISTINCT thread_id FROM checkpoints"
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def get_checkpoint_ids(
        self, thread_id: str, checkpoint_ns: str, limit: int
    ) -> list[str]:
        """Get checkpoint_ids ordered newest-first, limited to `limit`."""
        async with self._saver.lock:
            cursor = await self._saver.conn.execute(
                """
                SELECT checkpoint_id FROM checkpoints
                WHERE thread_id = ? AND checkpoint_ns = ?
                ORDER BY checkpoint_id DESC
                LIMIT ?
                """,
                (thread_id, checkpoint_ns, limit),
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def delete_checkpoints_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """Delete checkpoints NOT in keep_ids. Returns deleted count."""
        if not keep_ids:
            return 0

        placeholders = ",".join("?" * len(keep_ids))
        async with self._saver.lock:
            cursor = await self._saver.conn.execute(
                f"""
                DELETE FROM checkpoints
                WHERE thread_id = ? AND checkpoint_ns = ?
                AND checkpoint_id NOT IN ({placeholders})
                """,
                (thread_id, checkpoint_ns, *keep_ids),
            )
            await self._saver.conn.commit()
            return cursor.rowcount

    async def delete_writes_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """Delete writes NOT in keep_ids. Returns deleted count."""
        if not keep_ids:
            return 0

        placeholders = ",".join("?" * len(keep_ids))
        async with self._saver.lock:
            cursor = await self._saver.conn.execute(
                f"""
                DELETE FROM writes
                WHERE thread_id = ? AND checkpoint_ns = ?
                AND checkpoint_id NOT IN ({placeholders})
                """,
                (thread_id, checkpoint_ns, *keep_ids),
            )
            await self._saver.conn.commit()
            return cursor.rowcount

    async def count_writes_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """SQLite read-only mirror of :meth:`delete_writes_excluding`.

        T2b — same ``writes`` table + same NOT-IN-set semantics as the
        destructive arm so the dry-run count can never disagree with
        the destructive delete-count for the same keep-set (the
        parity pin). Empty ``keep_ids`` returns 0 (matches the
        destructive arm's early-return).
        """
        if not keep_ids:
            return 0
        placeholders = ",".join("?" * len(keep_ids))
        async with self._saver.lock:
            cursor = await self._saver.conn.execute(
                f"""
                SELECT COUNT(*) FROM writes
                WHERE thread_id = ? AND checkpoint_ns = ?
                AND checkpoint_id NOT IN ({placeholders})
                """,
                (thread_id, checkpoint_ns, *keep_ids),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def adelete_thread(self, thread_id: str) -> None:
        """Delete all checkpoint data for a thread."""
        await self._saver.adelete_thread(thread_id)

    async def find_excess_checkpoint_groups(
        self, max_per_thread: int
    ) -> list[tuple[str, str, int]]:
        """Find (thread_id, checkpoint_ns, count) groups exceeding max_per_thread."""
        async with self._saver.lock:
            cursor = await self._saver.conn.execute(
                """
                SELECT thread_id, checkpoint_ns, COUNT(*) as cnt
                FROM checkpoints
                GROUP BY thread_id, checkpoint_ns
                HAVING cnt > ?
                """,
                (max_per_thread,),
            )
            rows = await cursor.fetchall()
            return [(row[0], row[1], row[2]) for row in rows]

    async def find_all_thread_ns_pairs(self) -> list[tuple[str, str, int]]:
        """Return ALL (thread_id, checkpoint_ns, count) pairs — no HAVING filter.

        C3/D21: candidate enumeration for the blob prune needs every pair
        that has checkpoints (single-checkpoint threads included), unlike
        ``find_excess_checkpoint_groups`` whose HAVING clause would drop
        them.
        """
        async with self._saver.lock:
            cursor = await self._saver.conn.execute(
                """
                SELECT thread_id, checkpoint_ns, COUNT(*) as cnt
                FROM checkpoints
                GROUP BY thread_id, checkpoint_ns
                ORDER BY thread_id, checkpoint_ns
                """
            )
            rows = await cursor.fetchall()
            return [(row[0], row[1], row[2]) for row in rows]

    async def count_refs_for_blob_thread(
        self, thread_id: str, checkpoint_ns: str
    ) -> int:
        """SQLite stub — the SQLite saver has no checkpoint_blobs table.

        Blob prune is PostgreSQL-only (research-findings §3): the SQLite
        saver inlines channel values into the checkpoints row itself.
        Returns 0 refs; the C3 algorithm short-circuits SQLite adapters
        BEFORE the fail-safe check runs, so this 0 never trips the
        zero-refs ERROR path.
        """
        logger.debug(
            "count_refs_for_blob_thread: SQLite backend has no "
            "checkpoint_blobs table — returning 0"
        )
        return 0

    async def count_blobs_anti_join(
        self, thread_id: str, checkpoint_ns: str
    ) -> tuple[int, int]:
        """SQLite no-op — no checkpoint_blobs table exists for this backend."""
        logger.warning(
            "count_blobs_anti_join: SQLite backend has no checkpoint_blobs "
            "table — blob prune is a no-op (PostgreSQL-only operation)"
        )
        return (0, 0)

    async def count_blobs_referenced_only_by_excess(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> tuple[int, int]:
        """SQLite no-op — no checkpoint_blobs table exists for this backend.

        Mirror of :meth:`count_blobs_anti_join`'s SQLite stub: the v3.2
        projection helper is a PostgreSQL-only operation (dry-run path
        is gated to PG via the service's PG-isinstance check).
        """
        logger.warning(
            "count_blobs_referenced_only_by_excess: SQLite backend has "
            "no checkpoint_blobs table — projection is a no-op "
            "(PostgreSQL-only operation)"
        )
        return (0, 0)

    async def delete_blobs_anti_join(
        self, thread_id: str, checkpoint_ns: str
    ) -> tuple[int, int]:
        """SQLite no-op — no checkpoint_blobs table exists for this backend.

        Never deletes anything: SQLite channel values live inline in the
        checkpoints row (pruned by Operation D), so there is no separate
        blob bucket to prune on this backend.
        """
        logger.warning(
            "delete_blobs_anti_join: SQLite backend has no checkpoint_blobs "
            "table — blob prune is a no-op (PostgreSQL-only operation)"
        )
        return (0, 0)

    async def close(self) -> None:
        """Close the underlying aiosqlite connection held by the saver.

        The ``AsyncSqliteSaver`` keeps a long-lived ``aiosqlite.Connection``
        for the application's lifetime. We close it here so the background
        aiosqlite thread is released cleanly at shutdown.
        """
        conn = getattr(self._saver, "conn", None)
        if conn is None:
            return
        try:
            await conn.close()
            logger.debug("SQLite checkpointer connection closed")
        except Exception as e:
            # Closing during interpreter shutdown can raise benign errors;
            # log and move on so other shutdown steps still run.
            logger.warning(f"Error closing SQLite checkpointer connection: {e}")


class PostgresCheckpointerAdapter(CheckpointerAdapter):
    """Adapter wrapping AsyncPostgresSaver for checkpoint database operations.

    Uses an asyncpg connection pool for direct SQL access, adapting
    SQLite query patterns to PostgreSQL syntax.
    """

    def __init__(self, saver: Any, pool: Any) -> None:
        """Initialize the PostgreSQL checkpointer adapter.

        Args:
            saver: An AsyncPostgresSaver instance (pool-backed; see
                ``daemon.persistence.create_postgres_checkpointer``).
            pool: An asyncpg.Pool instance for direct SQL access.

        The ``saver`` argument is wrapped in a one-shot retry proxy
        (see :func:`_wrap_saver_with_connection_retry`) that retries
        each method call once on a connection-class failure. The
        proxy's surface is identical to the saver — non-method
        attribute access (``wrapper.conn``) passes through, so the
        adapter's ``close()`` continues to read ``saver.conn``
        transparently.
        """
        self._saver = _wrap_saver_with_connection_retry(saver)
        # Keep an unwrapped reference too: the wrapper is non-essential
        # for ``close()`` (which only needs ``.conn``), and tests that
        # want to assert against the real saver object can do so via
        # this reference.
        self._raw_saver = saver
        self._pool = pool

    @property
    def raw_saver(self) -> Any:
        """Return the retry proxy wrapping the AsyncPostgresSaver.

        The proxy carries one-shot connection-class retry coverage
        — see :func:`_wrap_saver_with_connection_retry` for the
        wrapper contract. ``StateGraph.compile(checkpointer=...)``
        routes LangGraph reads/writes through this proxy and gains
        the retry layer for free. ``isinstance(raw_saver,
        BaseCheckpointSaver)`` holds because the proxy class
        subclasses ``BaseCheckpointSaver`` (the gate at
        ``langgraph.types.ensure_valid_checkpointer`` would
        otherwise raise ``TypeError``).

        Renaming this property or unwrapping here would silently
        disable retry coverage on every LangGraph hot-path call —
        callers MUST continue to receive the proxy, not the bare
        saver. Tests that need the bare saver for assertions can
        reach it via ``adapter._raw_saver``.
        """
        return self._saver

    async def list_thread_ids(self) -> list[str]:
        """Return all distinct thread_ids from checkpoints table.

        Excludes the readiness-probe sentinel thread (incident
        2026-10-10 W1): the sentinel is not an instance thread and must
        never surface in maintenance Operation A's orphan scan. The
        sentinel write lands in ``checkpoint_writes`` only (no
        ``checkpoints`` row is ever created), so this guard is
        defense-in-depth today — but it keeps the sentinel out of
        orphan scans if the probe design ever writes a checkpoints row.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT thread_id FROM checkpoints "
                "WHERE thread_id != $1",
                CHECKPOINT_SENTINEL_THREAD_ID,
            )
            return [row["thread_id"] for row in rows]

    async def get_checkpoint_ids(
        self, thread_id: str, checkpoint_ns: str, limit: int
    ) -> list[str]:
        """Get checkpoint_ids ordered newest-first, limited to `limit`."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT checkpoint_id FROM checkpoints
                WHERE thread_id = $1 AND checkpoint_ns = $2
                ORDER BY checkpoint_id DESC
                LIMIT $3
                """,
                thread_id,
                checkpoint_ns,
                limit,
            )
            return [row["checkpoint_id"] for row in rows]

    async def delete_checkpoints_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """Delete checkpoints NOT in keep_ids. Returns deleted count."""
        if not keep_ids:
            return 0

        # asyncpg accepts a Python list for the ``$N::text[]`` parameter.
        # The leading NOT inverts the IN-semantics of ``= ANY()`` so we keep
        # only rows whose checkpoint_id is in keep_ids and delete the rest.
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM checkpoints
                WHERE thread_id = $1 AND checkpoint_ns = $2
                AND NOT (checkpoint_id = ANY($3::text[]))
                """,
                thread_id,
                checkpoint_ns,
                list(keep_ids),
            )
            # asyncpg.execute returns "DELETE n" where n is count
            if result:
                parts = result.split()
                if len(parts) >= 2:
                    return int(parts[1])
            return 0

    async def delete_writes_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """Delete writes NOT in keep_ids. Returns deleted count.

        NOTE: The PG LangGraph saver stores writes in the ``checkpoint_writes``
        table (NOT ``writes`` — that name is the SQLite table name).
        """
        if not keep_ids:
            return 0

        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM checkpoint_writes
                WHERE thread_id = $1 AND checkpoint_ns = $2
                AND NOT (checkpoint_id = ANY($3::text[]))
                """,
                thread_id,
                checkpoint_ns,
                list(keep_ids),
            )
            if result:
                parts = result.split()
                if len(parts) >= 2:
                    return int(parts[1])
            return 0

    async def count_writes_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """PG read-only mirror of :meth:`delete_writes_excluding`.

        T2b — same ``checkpoint_writes`` table (NOT ``writes`` — that's
        the SQLite table name) + same ``NOT (checkpoint_id = ANY(...))``
        semantics as the destructive arm so the dry-run count can never
        disagree with the destructive delete-count for the same
        keep-set (the parity pin). Empty ``keep_ids`` returns 0.
        """
        if not keep_ids:
            return 0
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt FROM checkpoint_writes
                WHERE thread_id = $1 AND checkpoint_ns = $2
                AND NOT (checkpoint_id = ANY($3::text[]))
                """,
                thread_id,
                checkpoint_ns,
                list(keep_ids),
            )
            return int(row["cnt"]) if row else 0

    async def adelete_thread(self, thread_id: str) -> None:
        """Delete all checkpoint data for a thread.

        Deletes from all three PG tables used by ``AsyncPostgresSaver``:
        - ``checkpoints``  — checkpoint JSONB
        - ``checkpoint_writes`` — pending writes (NOT ``writes`` — that's SQLite)
        - ``checkpoint_blobs`` — non-primitive channel values (no SQLite equivalent)

        All DELETE statements run inside a single transaction so a failure
        on any statement cannot leave the thread in a partially deleted
        state. Order: ``checkpoint_writes`` and ``checkpoint_blobs`` first
        (no FK to ``checkpoints``), then ``checkpoints`` last.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM checkpoint_writes WHERE thread_id = $1",
                    thread_id,
                )
                await conn.execute(
                    "DELETE FROM checkpoint_blobs WHERE thread_id = $1",
                    thread_id,
                )
                await conn.execute(
                    "DELETE FROM checkpoints WHERE thread_id = $1",
                    thread_id,
                )

    async def find_excess_checkpoint_groups(
        self, max_per_thread: int
    ) -> list[tuple[str, str, int]]:
        """Find (thread_id, checkpoint_ns, count) groups exceeding max_per_thread."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT thread_id, checkpoint_ns, COUNT(*) as cnt
                FROM checkpoints
                GROUP BY thread_id, checkpoint_ns
                HAVING COUNT(*) > $1
                """,
                max_per_thread,
            )
            return [
                (row["thread_id"], row["checkpoint_ns"], row["cnt"])
                for row in rows
            ]

    async def find_all_thread_ns_pairs(self) -> list[tuple[str, str, int]]:
        """Return ALL (thread_id, checkpoint_ns, count) pairs — no HAVING filter.

        C3/D21: blob-prune candidate enumeration. Unlike
        ``find_excess_checkpoint_groups`` (whose HAVING clause would drop
        single-checkpoint threads), this returns every group present.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT thread_id, checkpoint_ns, COUNT(*) as cnt
                FROM checkpoints
                GROUP BY thread_id, checkpoint_ns
                ORDER BY thread_id, checkpoint_ns
                """
            )
            return [
                (row["thread_id"], row["checkpoint_ns"], row["cnt"])
                for row in rows
            ]

    async def count_refs_for_blob_thread(
        self, thread_id: str, checkpoint_ns: str
    ) -> int:
        """Fail-safe pre-check: distinct (channel, version) refs across remaining rows.

        Counts DISTINCT (channel, version) pairs extracted from
        ``checkpoint -> 'channel_versions'`` over the REMAINING checkpoint
        rows of (thread_id, checkpoint_ns). Rows whose
        ``channel_versions`` is missing / not a JSON object contribute
        nothing (the CASE normalizes them to ``{}``) — so schema drift
        yields 0, which the caller treats as "extraction broken: SKIP +
        ERROR" instead of "delete everything".
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt FROM (
                    SELECT DISTINCT e.key AS channel, e.value AS version
                    FROM checkpoints c
                    CROSS JOIN LATERAL jsonb_each_text(
                        CASE
                            WHEN jsonb_typeof(c.checkpoint -> 'channel_versions') = 'object'
                            THEN c.checkpoint -> 'channel_versions'
                            ELSE '{}'::jsonb
                        END
                    ) AS e
                    WHERE c.thread_id = $1 AND c.checkpoint_ns = $2
                ) refs
                """,
                thread_id,
                checkpoint_ns,
            )
            return int(row["cnt"]) if row else 0

    async def count_blobs_anti_join(
        self, thread_id: str, checkpoint_ns: str
    ) -> tuple[int, int]:
        """DRY-RUN arm — report what the anti-join WOULD delete (SELECT only).

        Returns ``(would_delete_count, bytes_would_free)``. Uses the same
        ``_BLOB_ANTI_JOIN_PREDICATE`` as the destructive arm so the two
        can never disagree. Contains no DELETE statement.

        Deliberately stays READ COMMITTED (no SERIALIZABLE wrap): a
        read-only report cannot lose data, so there is no hazard to
        defend against — only the destructive arm needs the wrap.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt, COALESCE(SUM(OCTET_LENGTH(b.blob)), 0) AS bytes "
                "FROM checkpoint_blobs b WHERE" + _BLOB_ANTI_JOIN_PREDICATE,
                thread_id,
                checkpoint_ns,
            )
            if not row:
                return (0, 0)
            return (int(row["cnt"]), int(row["bytes"]))

    async def count_blobs_referenced_only_by_excess(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> tuple[int, int]:
        """v3.2 projection helper — blobs referenced ONLY by excess rows.

        Returns ``(count, bytes)`` for blobs of (thread_id, checkpoint_ns)
        whose (channel, version) IS referenced by ≥1 row whose
        ``checkpoint_id`` is NOT in ``keep_ids`` AND is NOT referenced by
        any row whose ``checkpoint_id`` IS in ``keep_ids``. Equivalently:
        blobs that are currently referenced but whose ONLY remaining
        referencers are excess rows Op D of this pass will delete —
        exactly the "after-D orphans" the v3.2 projection needs to
        surface.

        Set algebra vs the existing anti-join predicate:
        ``count_blobs_anti_join`` returns blobs with ZERO refs (the
        current ``now`` set); this helper returns blobs referenced by
        EXCESS rows only (the ``after − now`` delta). Their union is the
        post-D orphan set; subtracting ``now`` from the union gives
        ``after`` — this method computes ``after`` directly via
        EXISTS + NOT EXISTS rather than via the two-query difference
        (amendment R-1, "compute directly as 'referenced-by-excess-only'").

        Callers MUST pass a NON-empty ``keep_ids``: pairs whose keep
        set is empty (max_per_thread=0 edge) are SKIPPED upstream
        (``_compute_row_prune_dry_run`` never queries them) and
        contribute 0 to the projection, matching the destructive
        arm's skip. (With an empty ``keep_ids`` the EXISTS branch
        naturally expands to "any row refs b" and the NOT EXISTS
        branch is vacuously TRUE — the result would be the pair's
        full referenced set, which is exactly why the empty case must
        stay caller-excluded.)
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt, COALESCE(SUM(OCTET_LENGTH(b.blob)), 0) AS bytes
                FROM checkpoint_blobs b
                WHERE b.thread_id = $1 AND b.checkpoint_ns = $2
                  AND EXISTS (
                      SELECT 1
                      FROM checkpoints c_excess
                      WHERE c_excess.thread_id = b.thread_id
                        AND c_excess.checkpoint_ns = b.checkpoint_ns
                        AND (c_excess.checkpoint -> 'channel_versions' ->> b.channel) = b.version
                        AND NOT (c_excess.checkpoint_id = ANY($3::text[]))
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM checkpoints c_keep
                      WHERE c_keep.thread_id = b.thread_id
                        AND c_keep.checkpoint_ns = b.checkpoint_ns
                        AND (c_keep.checkpoint -> 'channel_versions' ->> b.channel) = b.version
                        AND c_keep.checkpoint_id = ANY($3::text[])
                  )
                """,
                thread_id,
                checkpoint_ns,
                list(keep_ids),
            )
            if not row:
                return (0, 0)
            return (int(row["cnt"]), int(row["bytes"]))

    async def delete_blobs_anti_join(
        self, thread_id: str, checkpoint_ns: str
    ) -> tuple[int, int]:
        """DESTRUCTIVE arm — run the anti-join DELETE (PG only).

        Deletes blobs of (thread_id, checkpoint_ns) not referenced by any
        remaining checkpoint's ``channel_versions`` in the same
        (thread_id, checkpoint_ns). Returns ``(deleted_count,
        bytes_freed)`` via ``DELETE ... RETURNING OCTET_LENGTH(blob)``.

        The DELETE runs inside an explicit SERIALIZABLE transaction with
        bounded retry on SQLSTATE 40001 (serialization_failure) / 40P01
        (deadlock_detected) — see the wrap rationale block at the method
        body. Retry budget: ``CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES``
        (daemon/constants.py); on exhaustion the pair is SKIPPED with an
        ERROR log and ``(0, 0)`` returned — this method never raises an
        isolation-family failure into the maintenance loop (Operation E's
        never-raise contract).

        DANGER — data-destroying. The only sanctioned call site is behind
        the structural gate in ``daemon/services/checkpoint_prune.py``
        (``blob_prune_destructive_enabled()``); with the env flags off no
        call path reaches this method.
        """
        # ── SERIALIZABLE wrap rationale (PR4 external-review CRITICAL #1) ──
        #
        # HAZARD: the DEFAULT AsyncPostgresSaver path on PG14+ (psycopg
        # autocommit + pipeline; aio.py:82 + aio.py:280-304) commits the
        # blob upsert and the checkpoint upsert as SEPARATE implicit
        # transactions — a µs-scale gap in which a concurrent reader can
        # see the blob but not the checkpoint row referencing it. (The
        # non-pipeline fallback IS atomic — aio.py:393-399.) If this
        # DELETE's snapshot lands in that gap, the anti-join sees the new
        # blob as unreferenced and deletes it, leaving every later aget to
        # silently reconstruct WITHOUT that channel. The idle gate in the
        # maintenance service is a PRECONDITION, not a lock — it narrows
        # but does not eliminate the window.
        #
        # WHAT THE WRAP DOES: under SERIALIZABLE, the DELETE's read-set
        # (checkpoints rows of the pair) vs a racing writer's write-set
        # (checkpoint row insert/update) forms a rw-antidependency; when
        # PostgreSQL's SSI detects a dangerous structure (two consecutive
        # rw-antidependency edges) involving this transaction, it aborts
        # exactly one side with SQLSTATE 40001 — and this side is the one
        # that can afford to yield: the retry re-acquires a pool
        # connection (fresh snapshot), re-evaluates the UNCHANGED
        # predicate, and the now-visible referencing checkpoint row
        # rescues its blob. Deadlocks (SQLSTATE 40P01) get the same
        # retry. Verified empirically on PG 14.22: a real 40001 fired
        # mid-DELETE when a second serializable participant supplied the
        # second rw-edge, and the retried delete spared the referenced
        # blob (see tests/integration/checkpoint_prune_real_saver.py::
        # TestRealSaverSerializableRetry).
        #
        # HONEST LIMIT (also verified empirically on PG 14.22, same
        # session): the µs-gap interleaving with ONLY the READ COMMITTED
        # aput as the racer does NOT trip SSI — a lone rw-out-edge is not
        # a dangerous structure and READ COMMITTED reads never register
        # in the SSI graph, so the delete can commit through the gap. The
        # wrap therefore hardens the conflict classes SSI actually
        # detects (deadlocks; any second serializable participant — e.g.
        # overlapping prune cycles or future serializable writers); the
        # residual single-racer window remains bounded by the idle-gate
        # precondition + the §6 backup
        # (docs/runbooks/checkpoint-blob-prune-restore.md). Do NOT
        # restate this wrap as eliminating the race.
        retries_left = CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES
        attempt = 0
        while True:
            attempt += 1
            try:
                # Same connection discipline as adelete_thread: acquire a
                # pool connection per attempt (a fresh connection ⇒ fresh
                # snapshot on retry) and let asyncpg's transaction context
                # issue BEGIN ISOLATION LEVEL SERIALIZABLE … COMMIT/ROLLBACK.
                async with self._pool.acquire() as conn:
                    async with conn.transaction(isolation="serializable"):
                        rows = await conn.fetch(
                            "DELETE FROM checkpoint_blobs b WHERE"
                            + _BLOB_ANTI_JOIN_PREDICATE
                            + " RETURNING OCTET_LENGTH(blob) AS n",
                            thread_id,
                            checkpoint_ns,
                        )
                bytes_freed = sum(
                    int(r["n"]) for r in rows if r["n"] is not None
                )
                return (len(rows), bytes_freed)
            except Exception as exc:
                # Duck-typed on the SQLSTATE so the check holds for any
                # driver exposing .sqlstate (asyncpg's SerializationError /
                # DeadlockDetectedError both do) without importing asyncpg
                # at module scope — this module must stay importable on
                # SQLite-only installs.
                sqlstate = getattr(exc, "sqlstate", None)
                if sqlstate not in ("40001", "40P01"):
                    # Not an isolation-family failure — propagate to the
                    # service's per-pair catch (its never-raise contract
                    # lives THERE; this method only converts retryable
                    # isolation failures into skip-not-raise).
                    raise
                if retries_left <= 0:
                    # Exhausted: SKIP the pair — zero rows deleted, never
                    # raise. Skipping is the SAFE direction (blobs survive;
                    # the next maintenance cycle retries in 15 min).
                    logger.error(
                        "[CheckpointPerf] blob_prune "
                        "SERIALIZABLE_RETRY_EXHAUSTED thread=%s ns=%s — "
                        "anti-join DELETE aborted with SQLSTATE %s on all "
                        "%d attempts; skipping pair, zero rows deleted "
                        "(safe direction; next maintenance cycle retries)",
                        thread_id[:8],
                        checkpoint_ns,
                        sqlstate,
                        attempt,
                    )
                    return (0, 0)
                retries_left -= 1
                backoff_s = 0.05 * (2 ** (attempt - 1))
                logger.warning(
                    "[CheckpointPerf] blob_prune "
                    "serializable_retry thread=%s ns=%s attempt=%d "
                    "sqlstate=%s (%s); retrying in %.0fms with a fresh "
                    "snapshot",
                    thread_id[:8],
                    checkpoint_ns,
                    attempt,
                    sqlstate,
                    "serialization_failure"
                    if sqlstate == "40001"
                    else "deadlock_detected",
                    backoff_s * 1000,
                )
                await asyncio.sleep(backoff_s)

    async def close(self) -> None:
        """Close the asyncpg pool and the saver's psycopg resource.

        Both resources are long-lived (one per process). The asyncpg pool
        (used by maintenance operations) is closed FIRST so no new
        maintenance queries can be issued; the saver's psycopg resource
        (single ``AsyncConnection`` in legacy paths, OR an
        ``AsyncConnectionPool`` after the incident 2026-10-10 fix) is
        closed LAST.

        Topology detection is duck-typed: the psycopg-pool's
        ``AsyncConnectionPool`` exposes ``.close()`` as an awaitable,
        and so does a plain ``AsyncConnection`` — the same ``await
        conn.close()`` call works for both. The detection criterion
        is the presence of a ``pool``-only marker attribute
        (``get_stats`` is pool-specific and absent on a bare
        ``AsyncConnection``) so we log a precise topology name and
        can branch later if needed.

        Failures are logged and swallowed so the rest of the shutdown
        sequence can continue. The original errors are preserved in the
        log so they can be debugged post-mortem.
        """
        # Close the asyncpg pool first (used by maintenance operations)
        if self._pool is not None:
            try:
                await self._pool.close()
                logger.debug("PostgreSQL checkpointer asyncpg pool closed")
            except Exception as e:
                logger.warning(f"Error closing PostgreSQL checkpointer pool: {e}")

        # Close the saver's psycopg resource (connection OR pool — see
        # incident 2026-10-10 fix). The retry wrapper passes ``.conn``
        # attribute access through transparently, so we read it the same
        # way as before.
        conn = getattr(self._saver, "conn", None)
        if conn is None:
            return
        # Topology detection: AsyncConnectionPool exposes ``get_stats`` /
        # ``min_size`` / ``max_size``; AsyncConnection does not. We do NOT
        # import AsyncConnectionPool here (SQLite-only installs must not
        # pay for it). Duck-typed on the pool-only ``get_stats`` attribute
        # — psycopg_pool 3.3.1 guarantees this attribute exists on the
        # pool object (pool_async.py: ``self._pool`` is a deque of
        # connections and ``get_stats`` is a public method).
        is_pool = hasattr(conn, "get_stats") and hasattr(conn, "min_size")
        topology = "pool" if is_pool else "single-connection"
        try:
            await conn.close()
            logger.debug(
                "PostgreSQL checkpointer saver resource closed "
                "(topology=%s)",
                topology,
            )
        except Exception as e:
            logger.warning(
                "Error closing PostgreSQL checkpointer saver %s: %s",
                topology,
                e,
            )
