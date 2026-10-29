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
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

try:
    # Incident 2026-10-10 review fix: the retry proxy subclasses
    # ``BaseCheckpointSaver`` so ``StateGraph.compile`` (via
    # ``ensure_valid_checkpointer``) accepts it as a valid saver.
    from langgraph.checkpoint.base import BaseCheckpointSaver
except ImportError:
    # Fenced fallback — covers ONLY older langgraph pins where the
    # ``langgraph.checkpoint.base`` submodule does not exist (the
    # 1.0.x-era layout had a different base path) or a packaging
    # accident. The current pin (langgraph 1.0.9 /
    # langgraph-checkpoint 3.1.x) ships the submodule and takes the
    # try-branch. Under the fallback the daemon loses the
    # ``isinstance`` gate but keeps the proxy functional: the proxy
    # inherits from ``object`` so its surface is identical to the
    # language-level contract — only the LangGraph compile-time gate
    # would then raise TypeError.
    BaseCheckpointSaver = object  # type: ignore[assignment,misc]

from daemon._redact import redact_exc_str
# P2 T3 charge seam (resource-cost-control): stdlib-only module, no
# import-cycle risk — see daemon/services/resource_monitor/charge.py.
from daemon.services.resource_monitor.charge import (
    bounded_deep_size,
    is_enabled as _charge_enabled,
    note_checkpoint_read,
)
# Track-1 item 4 (read instrumentation): the metric registry is
# stdlib-only (no daemon imports) and the perf helper module depends
# on it — no import-cycle risk.
from daemon.checkpoint_metrics import (
    checkpoint_read_measurement_errors_total,
    ensemble_checkpoint_cache_hit_total,
    ensemble_checkpoint_cache_miss_total,
)
from daemon.checkpoint_perf import (
    checkpoint_perf_logs_enabled,
    log_cache_hit,
    log_checkpoint_read,
)
from daemon.checkpoint_read_cache import (
    CheckpointReadCache,
    checkpoint_read_cache_enabled,
    extract_cache_key,
    extract_thread_ids_from_write,
    invalidate_cached_reads,
)
from daemon.constants import (
    CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE,
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


def _charge_instance_from_args(args: tuple, kwargs: dict) -> Optional[str]:
    """Extract the requesting instance_id from a checkpoint READ call.

    MECHANISM (pinned for the P2 charge seam, T3 / plan D3): LangGraph
    invokes ``aget`` / ``aget_tuple`` with the runnable config as the
    first positional argument, and every ensemble graph runs with
    ``config = {"configurable": {"thread_id": instance_id}}``
    (instance_messaging.py:1195/:1830/:1844, manager.py:11275/:11296;
    ``thread_id == instance_id`` per manager.py:3162). The read call
    site therefore ALREADY carries the instance identity — no
    context-var threading is needed. Unattributable calls (no config /
    no thread_id) are skipped, never guessed.
    """
    if args:
        cfg = args[0]
    else:
        cfg = kwargs.get("config")
    if not isinstance(cfg, dict):
        return None
    configurable = cfg.get("configurable")
    if not isinstance(configurable, dict):
        return None
    thread_id = configurable.get("thread_id")
    if isinstance(thread_id, str) and thread_id:
        return thread_id
    return None


def _shared_payload_size(result: Any) -> tuple[Optional[int], Optional[BaseException]]:
    """ONE bounded deep-size walk for BOTH read consumers (M-2).

    The read path has TWO consumers of the decoded payload's size:

    * the resource-charge seam (``note_checkpoint_read``), which charges
      the bytes to the instance's resource budget;
    * the read instrumentation (``log_checkpoint_read``), which records
      the bytes + rows in the metric registry.

    Both used to run their OWN ``bounded_deep_size`` walk over the SAME
    payload — two O(payload) walks per read, ON THE EVENT LOOP. This
    helper performs the walk ONCE and hands the number to both.

    Returns:
        ``(size, error)`` — ``size`` is the measured byte count (``0``
        for a MISS — a miss is a read with no payload), or ``None`` when
        the walk FAILED. ``error`` is the exception that broke the walk
        (``None`` on success). The walk NEVER raises out: the caller
        counts the failure in the fail-open tripwire.
    """
    if result is None:
        return 0, None
    try:
        size, _nodes, _partial = bounded_deep_size(result)
        return size, None
    except Exception as exc:  # noqa: BLE001 — fail-open by contract
        return None, exc


def _charge_checkpoint_read(
    args: tuple,
    kwargs: dict,
    result: Any,
    *,
    size: Optional[int] = None,
) -> None:
    """Charge a decoded checkpoint READ payload to the requesting
    instance (P2 T3). Fires ONCE per successful outer call — the retry
    wrapper (``_call_with_retry``) has already settled when this runs,
    so a transient connection retry NEVER double-charges (AC1). All
    failure modes are swallowed inside the charge module: observability
    must never break the read path (R1).

    ``size`` — the PRE-COMPUTED deep size (M-2: ONE walk, two
    consumers). ``None`` means the shared walk FAILED ⇒ nothing is
    charged (there is no number to charge) and the tripwire already
    counted the failure.
    """
    if result is None:
        return
    instance_id = _charge_instance_from_args(args, kwargs)
    if instance_id is None:
        return
    if size is None:
        # The shared walk failed — the tripwire owns the failure
        # signal; the charge is skipped (there is no size to charge).
        return
    note_checkpoint_read(instance_id, result, size=size)


# ── Read instrumentation (plan Track-1 item 4) ─────────────────────────────
#
# The read-amplification audit's ONE adopted fix: measure BYTES and ROWS
# per read at the PROXY chokepoint (the seam every daemon read surface
# traverses — see the architecture recommendation §4.1). The measurement
# is FAIL-OPEN by construction: it runs AFTER the read settled, it
# never touches the result, and every failure inside it is swallowed —
# the read result is ALWAYS returned as-is.
#
# WHAT "ROWS" MEANS HERE (honest labelling): the server's row counter
# is not observable at this seam, so the count is DERIVED from the
# returned structure:
#
#   * 1 — the checkpoint row itself;
#   * +1 per channel value that is NOT a primitive — the PG saver
#     inlines primitives (``None``/``str``/``int``/``float``/``bool``)
#     in the checkpoint row and stores everything else as a SEPARATE
#     blob row (``langgraph/checkpoint/postgres/aio.py`` aput: "inline
#     primitive values in checkpoint table / others are stored in blobs
#     table");
#   * +1 per pending write — each pending write is its own row.
#
# WHAT "BYTES" MEANS HERE: the DECODED payload size, estimated with the
# bounded deep-size walk (the SAME estimator the resource-monitor
# charge seam uses on this very payload). It is NOT the wire size — the
# serializer's framing overhead is not included.
#
# ONE WALK, TWO CONSUMERS (M-2): the walk happens ONCE per read, at
# the proxy chokepoint (:func:`_observe_read`), and the resulting
# number is handed to BOTH the charge seam and the instrumentation.
# The payload is never walked twice for the same read.


def _derive_read_rows(op: str, result: Any) -> int:
    """Derive the number of rows a settled read returned.

    See the block comment above for the derivation rules. Never raises
    for an unexpected shape: an unreadable payload yields 0 (the
    measurement is advisory).
    """
    if result is None:
        return 0
    checkpoint: Any = None
    pending_writes: Any = None
    if op == "aget_tuple":
        # CheckpointTuple(config, checkpoint, metadata, parent_config,
        # pending_writes) — a NamedTuple, so attribute access is the
        # canonical read (the tuple is NOT unpacked here: a foreign
        # shape must not raise).
        checkpoint = getattr(result, "checkpoint", None)
        pending_writes = getattr(result, "pending_writes", None)
    else:
        checkpoint = result
    rows = 0
    if isinstance(checkpoint, dict):
        rows += 1  # the checkpoint row
        channel_values = checkpoint.get("channel_values")
        if isinstance(channel_values, dict):
            for value in channel_values.values():
                if value is None or isinstance(value, (str, int, float, bool)):
                    continue  # inlined in the checkpoint row — no blob row
                rows += 1  # one blob row
    if isinstance(pending_writes, (list, tuple)):
        rows += len(pending_writes)
    return rows


def _record_read_measurement_error(op: str, exc: BaseException) -> None:
    """Count a measurement failure — the FAIL-OPEN tripwire.

    The measurement never breaks a read, so this counter is the ONLY
    signal that the instrumentation itself stopped working. The counter
    increment is itself guarded: a broken metric registry must not
    turn into a broken read.
    """
    try:
        checkpoint_read_measurement_errors_total.inc()
    except Exception:  # noqa: BLE001 — the tripwire must never raise
        logger.warning(
            "[CheckpointPerf] read instrumentation counter update failed "
            "(op=%s) — the failure counter itself is unavailable",
            op,
            exc_info=True,
        )
    logger.warning(
        "[CheckpointPerf] read instrumentation failed (op=%s): %s: %s — "
        "the read itself was NOT affected (fail-open)",
        op,
        type(exc).__name__,
        exc,
    )


def _observe_read(
    op: str,
    args: tuple,
    kwargs: dict,
    result: Any,
    *,
    size: Optional[int] = None,
) -> Optional[int]:
    """Observe ONE settled read: ONE size walk, BOTH consumers (M-2).

    The read path has two consumers of the decoded payload's size — the
    resource-charge seam (per-instance byte budget) and the read
    instrumentation (bytes + rows in the metric registry). Both used to
    run their OWN bounded deep-size walk over the SAME payload: two
    O(payload) walks per read, ON THE EVENT LOOP.

    This seam walks the payload ONCE and hands the number to both.

    ``size`` — a PRE-COMPUTED deep size (the read-cache HIT path hands
    the size stored at PUT time, so a hit charges the exact same byte
    count a real read would have walked — no second walk; the M-2
    one-walk discipline extended to the cache). ``None`` (the default,
    and every pre-cache call site) walks here.

    Returns the size that was walked (or handed in), or ``None`` when
    nobody needed it — the read-cache PUT uses this to avoid a second
    walk when one already happened.

    THE KILL-SWITCH: ``CHECKPOINT_PERF_LOGS=0`` turns the
    instrumentation OFF (the recording is skipped — see
    :func:`daemon.checkpoint_perf.log_checkpoint_read`). When the
    resource monitor is ALSO off, NOTHING needs the size ⇒ the walk is
    skipped entirely (no O(payload) work on the hot path). The charge
    seam is INDEPENDENT of the perf kill-switch: it keeps working
    (and keeps walking) while the monitor is enabled.
    """
    charge_on = _charge_enabled()
    perf_on = checkpoint_perf_logs_enabled()
    if not charge_on and not perf_on:
        # Nobody needs the size — the walk is pure waste.
        return None
    if size is None:
        size, size_error = _shared_payload_size(result)
    else:
        size_error = None
    if charge_on:
        _charge_checkpoint_read(args, kwargs, result, size=size)
    if perf_on:
        _measure_checkpoint_read(
            op, args, kwargs, result, bytes_=size, size_error=size_error
        )
    return size


def _measure_checkpoint_read(
    op: str,
    args: tuple,
    kwargs: dict,
    result: Any,
    *,
    bytes_: Optional[int] = None,
    size_error: Optional[BaseException] = None,
) -> None:
    """Measure ONE settled checkpoint read (bytes + rows) — FAIL-OPEN.

    Called from the proxy forwarders AFTER the retry wrapper settled, so
    a transient connection retry is measured ONCE (the same
    once-per-outer-call contract the charge seam follows).

    ``bytes_`` — the PRE-COMPUTED deep size of the payload (M-2: the
    walk happens ONCE per read and is SHARED with the charge seam; this
    function never walks the payload itself). ``None`` means the shared
    walk failed — ``size_error`` then carries the exception, and the
    failure is counted in the tripwire (never raised).

    NEVER raises: every failure is counted in
    ``checkpoint_read_measurement_errors_total`` and swallowed. The read
    result is never inspected destructively — the payload is only
    measured.
    """
    try:
        if size_error is not None:
            raise size_error
        if result is None:
            # A MISS is still a read (a DB round trip that returned no
            # checkpoint): count it with zero bytes / zero rows.
            bytes_ = 0
            rows = 0
        else:
            bytes_ = max(0, int(bytes_ or 0))
            rows = _derive_read_rows(op, result)
        thread_id = _charge_instance_from_args(args, kwargs) or ""
    except Exception as exc:  # noqa: BLE001 — fail-open by contract
        _record_read_measurement_error(op, exc)
        return
    try:
        log_checkpoint_read(op, thread_id, bytes_=bytes_, rows=rows)
    except Exception as exc:  # noqa: BLE001 — fail-open by contract
        _record_read_measurement_error(op, exc)


def _inc_cache_metric(counter: Any, op: str) -> None:
    """Increment a read-cache counter — FAIL-OPEN (never break a read).

    Same swallow-and-log contract as the measurement tripwire
    (:func:`_record_read_measurement_error`): observability must never
    break the read path. The registry inc is lock+arithmetic (no I/O),
    so this guard is belt-and-braces.
    """
    try:
        counter.inc()
    except Exception:  # noqa: BLE001 — fail-open by contract
        logger.warning(
            "[CheckpointPerf] read-cache counter update failed (op=%s) — "
            "the read itself was NOT affected",
            op,
            exc_info=True,
        )


def _wrap_saver_with_connection_retry(saver: Any) -> Any:
    """Wrap a saver with one-shot retry on connection-class failures.

    The wrapper forwards every attribute access and method call to the
    underlying saver. Every CONCRETE public member of the pinned
    ``BaseCheckpointSaver`` surface has an EXPLICIT forwarder (the MRO
    rule: base-concrete attributes resolve via MRO and ``__getattr__``
    never fires for them — see the proxy class docstring). Only the
    six hot-path async ops carry the retry layer. Non-method access
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
    # S2 (agent-secrets P2): lazy factory-call import (mirrors the
    # long_tool_nudge ToolNode discipline — call-time resolution so
    # the repo conftest's langgraph mock lifecycle can't freeze a
    # stale binding, and so a scrub-module import failure surfaces
    # HERE, at wrapper build, never silently mid-write). Before any
    # SecretService exists the helper is an identity function.
    from daemon.services.secret_persistence_scrub import (
        scrub_saver_write_args,
    )

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
        set is the six hot-path async ops (``aget``, ``aget_tuple``,
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
        ``get_delta_channel_history``) — the proxy forwards ALL of
        them regardless. Their pinned-upstream behavior is NOT
        uniformly NotImplementedError (verified against the pinned
        ``AsyncPostgresSaver``, aio.py):

        * ``get_tuple`` / ``list`` / ``delete_thread`` — real sync
          wrappers: they drive the async twin via
          ``run_coroutine_threadsafe(...).result()`` and carry a
          main-loop guard that raises
          ``asyncio.InvalidStateError`` when invoked ON the saver's
          own running loop (a worker thread with no running loop
          passes the guard and blocks on ``.result()``).
        * ``put`` / ``put_writes`` — real sync wrappers WITHOUT the
          main-loop guard: calling either from the daemon's async
          loop would DEADLOCK on ``.result()`` (latent hazard —
          this async-only daemon has no sync caller today; never
          call them from the loop).
        * ``get`` / ``get_delta_channel_history`` — real base-class
          defaults that delegate to ``self.get_tuple(...)``, so they
          inherit ``get_tuple``'s guard/blocking semantics
          (``get_delta_channel_history`` is a beta surface).
        * ``copy_thread`` / ``delete_for_runs`` / ``prune`` —
          upstream NotImplementedError stubs: a stray sync caller
          gets the saver's own upstream-classified
          NotImplementedError, not a proxy artifact.

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

        __slots__ = ("_saver", "_read_cache")

        def __init__(self, saver: Any) -> None:
            self._saver = saver
            # Read-cache substrate (plan §4.1/§4.2): one cache per
            # proxy (per saver). INERT while
            # ``CHECKPOINT_READ_CACHE_ENABLED`` is off — every read
            # takes the miss path (identical to today); the write
            # forwarders' invalidation hooks fire as no-ops; the
            # hit/miss counters still emit (all misses).
            self._read_cache = CheckpointReadCache()

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
                    # W2 (incident 2026-10-10): libpq messages carry
                    # host:port / socket / dbname identifiers — mask
                    # them before the log line is built.
                    redact_exc_str(exc),
                )
                # Second (and final) attempt. If this also fails, re-raise
                # unchanged so the upstream pipeline classifies the error
                # exactly as it did before the wrapper existed.
                return await method(*args, **kwargs)

        # ── Write invalidation (plan §4.3.A) ─────────────────────────────
        #
        # EVERY write forwarder invalidates BEFORE delegating the write
        # (an invalidation that raced AFTER the write could re-cache a
        # pre-write read that landed between the two). The runtime
        # self-check below asserts post-write cache-emptiness for the
        # written thread — an explicit branch, NEVER an ``assert``
        # (house rule: must survive ``python -O``) and NEVER a raise
        # (a broken invariant logs ERROR + forces the invalidation; it
        # must not break the production write).

        def _invalidate_for_write(self, method_name: str, args: tuple, kwargs: dict) -> None:
            thread_ids = extract_thread_ids_from_write(method_name, args, kwargs)
            if thread_ids:
                self._read_cache.invalidate_many(thread_ids)
            else:
                # Un-attributable write — stale-is-worse-than-cold.
                self._read_cache.clear()

        def _assert_post_write_empty(self, method_name: str, args: tuple, kwargs: dict) -> None:
            """Runtime self-check: the written thread left the cache (§4.3.C)."""
            thread_ids = extract_thread_ids_from_write(method_name, args, kwargs)
            if thread_ids:
                for thread_id in thread_ids:
                    if self._read_cache.thread_cached(thread_id):
                        logger.error(
                            "[CheckpointReadCache] thread %.8s still cached "
                            "after %s — invalidation seam broken (stale-read "
                            "risk); forcing invalidation",
                            thread_id,
                            method_name,
                        )
                        self._read_cache.invalidate(thread_id)
            elif len(self._read_cache) != 0:
                logger.error(
                    "[CheckpointReadCache] cache non-empty after "
                    "un-attributable write %s — forcing full clear",
                    method_name,
                )
                self._read_cache.clear()

        # ── Retry-wrapped async methods (hot checkpoint ops) ─────────
        # The six methods below carry the one-shot connection-retry
        # layer. They are the LangGraph hot path (reads/writes during
        # pregel turns). Everything else on the base surface forwards
        # WITHOUT retry — see the class docstring for the rationale.
        #
        # S2 — agent-secrets P2 persisted-snapshot scrub (design §2.2,
        # p2-architecture-recommendation): the four WRITE methods scrub
        # a COPY of the snapshot (AIMessage tool-call args + the
        # OpenAI-flavor additional_kwargs fallback + ToolMessage
        # content) before the wrapped saver serializes it. Live state
        # objects are NEVER touched — at this boundary the checkpoint's
        # ``channel_values`` carry LIVE objects (``chan.checkpoint()``
        # returns the channel's live value), so the transform mirrors
        # the saver's own ``copy = checkpoint.copy()`` discipline and
        # hands the wrapped saver scrubbed deep copies. Idempotent
        # under re-save (markers fullmatch the skip guard). Before any
        # SecretService exists the helper is an identity function
        # (zero behavior change — the K-guard contract below stays
        # green); a scrub failure is swallowed + logged (never fails
        # a checkpoint write). This is the sibling of the
        # ``redact_exc_str`` log-scrub in ``_call_with_retry``.
        # ── Read-cache substrate helpers (plan §4.1/§4.2/§4.4) ──────────
        #
        # The cache lives HERE — above ``_call_with_retry`` and above
        # ``_charge_checkpoint_read`` — because the proxy's read
        # forwarders are the ONE chokepoint every daemon read surface
        # traverses. ``cache_bypass=True`` (readiness sentinel) skips
        # ONLY the cache layer: the sentinel's health semantics require
        # the REAL production path (retry + lock + charge).
        #
        # Counters (observable-off contract, plan §4.5): a non-bypass
        # read increments hit or miss in BOTH switch states — with the
        # kill-switch OFF every read counts as a miss (disabled is
        # OBSERVABLE, not silent). Bypass reads count NEITHER.

        def _serve_cache_hit(
            self,
            op: str,
            args: tuple,
            kwargs: dict,
            served: Any,
            thread_id: str,
            size: int,
            t0: float,
        ) -> None:
            """One HIT: counter + structured line + CHARGE (§4.4).

            The payload was still materialized and decoded once (at
            PUT time), so the resource charge keeps its baseline —
            never silently uncharged. The precomputed ``size`` is
            handed to ``_observe_read`` so a hit does NOT re-walk the
            payload (M-2: one walk per materialization).
            """
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            _inc_cache_metric(ensemble_checkpoint_cache_hit_total, op)
            try:
                log_cache_hit(thread_id, elapsed_ms, bytes_=size)
            except Exception:  # noqa: BLE001 — fail-open observability
                logger.warning(
                    "[CheckpointPerf] cache-hit log line failed (op=%s)",
                    op,
                    exc_info=True,
                )
            _observe_read(op, args, kwargs, served, size=size)

        async def aget(self, *args, **kwargs):
            # P2 T3 charge seam: decoded payload charged AFTER the retry
            # wrapper settles ⇒ transient retry charges ONCE (AC1).
            # Track-1 item 4: read instrumentation (bytes + rows) at the
            # proxy chokepoint. FAIL-OPEN — a measurement failure never
            # breaks or slows the read; the result is returned as-is.
            #
            # M-2: ONE bounded deep-size walk per read, SHARED by both
            # consumers (the charge seam + the instrumentation). The walk
            # used to run TWICE over the SAME payload (twice the
            # O(payload) work, on the event loop).
            #
            # Read cache (plan §4.1): consult BEFORE the saver; a hit
            # serves ``cached.checkpoint`` (the base ``aget`` contract —
            # ``base/__init__.py``: ``if value := await self.aget_tuple``.
            # A miss keeps TODAY's exact delegation (the K-guard
            # contract requires ``proxy.aget`` to reach the WRAPPED
            # saver's ``aget`` override, so aget does NOT delegate to
            # the proxy's own aget_tuple); aget misses therefore do NOT
            # populate the cache — only aget_tuple does (one artifact,
            # plan §4.2).
            cache_bypass = kwargs.pop("cache_bypass", False)
            if not cache_bypass:
                key = extract_cache_key(args, kwargs)
                if checkpoint_read_cache_enabled() and key is not None:
                    t0 = time.perf_counter()
                    hit = self._read_cache.get(*key)
                    if hit is not None:
                        cached, size = hit
                        self._serve_cache_hit(
                            "aget", args, kwargs, cached.checkpoint,
                            key[0], size, t0,
                        )
                        return cached.checkpoint
                _inc_cache_metric(ensemble_checkpoint_cache_miss_total, "aget")
            result = await self._call_with_retry("aget", args, kwargs)
            _observe_read("aget", args, kwargs, result)
            return result

        async def aget_tuple(self, *args, **kwargs):
            # P2 T3 charge seam: same once-per-outer-call contract; the
            # SAME single shared walk (M-2).
            #
            # Read cache (plan §4.1/§4.2): the POPULATING read — the
            # cached artifact is the post-deserialize CheckpointTuple
            # exactly as the saver returned it (the serde already ran;
            # its cost is what the cache amortizes). ``put`` stores a
            # SHALLOW COPY and reuses the walk ``_observe_read`` already
            # paid (or walks once itself when charge+perf are both off).
            cache_bypass = kwargs.pop("cache_bypass", False)
            key = None if cache_bypass else extract_cache_key(args, kwargs)
            if not cache_bypass:
                if checkpoint_read_cache_enabled() and key is not None:
                    t0 = time.perf_counter()
                    hit = self._read_cache.get(*key)
                    if hit is not None:
                        cached, size = hit
                        self._serve_cache_hit(
                            "aget_tuple", args, kwargs, cached,
                            key[0], size, t0,
                        )
                        return cached
                _inc_cache_metric(ensemble_checkpoint_cache_miss_total, "aget_tuple")
            result = await self._call_with_retry("aget_tuple", args, kwargs)
            walked = _observe_read("aget_tuple", args, kwargs, result)
            if (
                not cache_bypass
                and key is not None
                and result is not None
                and checkpoint_read_cache_enabled()
            ):
                self._read_cache.put(key[0], key[1], result, size=walked)
            return result

        async def aput(self, *args, **kwargs):
            args, kwargs = scrub_saver_write_args("aput", args, kwargs)
            self._invalidate_for_write("aput", args, kwargs)
            result = await self._call_with_retry("aput", args, kwargs)
            self._assert_post_write_empty("aput", args, kwargs)
            return result

        async def aput_writes(self, *args, **kwargs):
            args, kwargs = scrub_saver_write_args(
                "aput_writes", args, kwargs
            )
            self._invalidate_for_write("aput_writes", args, kwargs)
            result = await self._call_with_retry("aput_writes", args, kwargs)
            self._assert_post_write_empty("aput_writes", args, kwargs)
            return result

        async def aget_delta_channel_history(self, *args, **kwargs):
            # A READ of a DIFFERENT artifact (delta channel history) —
            # deliberately NOT cached (the read cache serves whole
            # CheckpointTuples only) and needs no bypass kwarg (it
            # never consults the cache).
            return await self._call_with_retry(
                "aget_delta_channel_history", args, kwargs
            )

        async def adelete_thread(self, *args, **kwargs):
            # Public method on the AsyncPostgresSaver aio.py surface
            # (pinned aio.py:340 in langgraph-checkpoint-postgres
            # 3.1.x). Forwarded through the same retry wrapper as
            # the other public methods so a mid-cursor-open PG
            # failure during a thread drop is also recovered once.
            self._invalidate_for_write("adelete_thread", args, kwargs)
            result = await self._call_with_retry(
                "adelete_thread", args, kwargs
            )
            self._assert_post_write_empty("adelete_thread", args, kwargs)
            return result

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
        # deliberately the six hot-path ops). ALL of them are WRITES on
        # the checkpoint store ⇒ each carries the same
        # invalidate-before-delegate + post-write self-check hooks as
        # the hot-path write forwarders (plan §4.3.A: EVERY write
        # forwarder).
        async def acopy_thread(self, *args, **kwargs):
            self._invalidate_for_write("acopy_thread", args, kwargs)
            result = await self._saver.acopy_thread(*args, **kwargs)
            self._assert_post_write_empty("acopy_thread", args, kwargs)
            return result

        async def adelete_for_runs(self, *args, **kwargs):
            self._invalidate_for_write("adelete_for_runs", args, kwargs)
            result = await self._saver.adelete_for_runs(*args, **kwargs)
            self._assert_post_write_empty("adelete_for_runs", args, kwargs)
            return result

        async def aprune(self, *args, **kwargs):
            self._invalidate_for_write("aprune", args, kwargs)
            result = await self._saver.aprune(*args, **kwargs)
            self._assert_post_write_empty("aprune", args, kwargs)
            return result

        # Sync twins — concrete on the base; their pinned-upstream
        # behavior varies per twin (real sync wrappers for most —
        # see the class docstring's sync-twins breakdown). The
        # forwarders route to the wrapped saver's own implementations
        # either way, so a stray sync caller gets upstream behavior,
        # not a proxy artifact. NO retry (never invoked by the daemon).
        # ``put`` / ``put_writes`` carry the SAME S2 scrub as their
        # async twins (design §2.2 pins :386/:389 + :298/:301 alike)
        # so a future sync caller cannot become the unscrubbed hole;
        # the WRITE twins carry the same invalidate-before-delegate +
        # post-write self-check hooks for the same reason (a future
        # sync caller cannot become the un-invalidated hole either).
        def get_tuple(self, *args, **kwargs):
            return self._saver.get_tuple(*args, **kwargs)

        def get(self, *args, **kwargs):
            return self._saver.get(*args, **kwargs)

        def list(self, *args, **kwargs):
            return self._saver.list(*args, **kwargs)

        def put(self, *args, **kwargs):
            args, kwargs = scrub_saver_write_args("put", args, kwargs)
            self._invalidate_for_write("put", args, kwargs)
            result = self._saver.put(*args, **kwargs)
            self._assert_post_write_empty("put", args, kwargs)
            return result

        def put_writes(self, *args, **kwargs):
            args, kwargs = scrub_saver_write_args(
                "put_writes", args, kwargs
            )
            self._invalidate_for_write("put_writes", args, kwargs)
            result = self._saver.put_writes(*args, **kwargs)
            self._assert_post_write_empty("put_writes", args, kwargs)
            return result

        def delete_thread(self, *args, **kwargs):
            self._invalidate_for_write("delete_thread", args, kwargs)
            result = self._saver.delete_thread(*args, **kwargs)
            self._assert_post_write_empty("delete_thread", args, kwargs)
            return result

        def delete_for_runs(self, *args, **kwargs):
            self._invalidate_for_write("delete_for_runs", args, kwargs)
            result = self._saver.delete_for_runs(*args, **kwargs)
            self._assert_post_write_empty("delete_for_runs", args, kwargs)
            return result

        def copy_thread(self, *args, **kwargs):
            self._invalidate_for_write("copy_thread", args, kwargs)
            result = self._saver.copy_thread(*args, **kwargs)
            self._assert_post_write_empty("copy_thread", args, kwargs)
            return result

        def prune(self, *args, **kwargs):
            self._invalidate_for_write("prune", args, kwargs)
            result = self._saver.prune(*args, **kwargs)
            self._assert_post_write_empty("prune", args, kwargs)
            return result

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
    async def has_checkpoint(self, thread_id: str) -> bool:
        """Existence probe for the ROOT namespace — bounded, decode-free.

        Track-1 quick-win (checkpoint read-path plan, item 2). Answers
        exactly one question — "does a checkpoint row exist for this
        thread?" — with an existence query instead of a FULL saver
        read.

        Why this exists on the adapter surface (and not on the saver):
        the saver's read path takes the saver's single ``asyncio.Lock``
        and deserializes the ENTIRE checkpoint (checkpoint + channel
        values + blobs) before the caller can look at one field of it.
        The PostgreSQL adapter already owns a SEPARATE asyncpg pool
        for direct SQL, so the probe runs there and never touches the
        saver lock.

        Namespace semantics are IDENTICAL to the saver lookup it
        replaces: LangGraph resolves
        ``config["configurable"].get("checkpoint_ns", "")`` — i.e. the
        ROOT namespace — and returns "no checkpoint" when no
        ``checkpoints`` row matches. This probe is therefore scoped to
        the root namespace only.

        Args:
            thread_id: The instance id (LangGraph thread_id).

        Returns:
            True iff a checkpoint row exists for ``thread_id`` in the
            root namespace.
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
        exhaustion it logs ERROR, skips the chunk and returns the
        REAL PARTIAL totals accumulated so far — isolation-family
        failures never raise into maintenance. The early-stop reason
        is additionally published on the PG instance
        (``last_pair_stop_reason`` / ``last_pair_chunks_skipped``) so
        the caller can surface a partial-completion signal in the
        audit summary (W1, fix cycle 1). The dry-run arm
        (``count_blobs_anti_join``) stays READ COMMITTED:
        a read-only report has no concurrency hazard to defend against.

        DANGER — data-destroying. The ONLY sanctioned call site is behind
        the ``if not destructive: ... continue`` guard in
        ``daemon/services/checkpoint_prune.py::prune_unreferenced_blobs``;
        with ``destructive=False`` (manual preview path) no call path
        reaches this method. The dual-arm env-flag gate is gone — see
        feature/blob-prune-default-on.

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

    async def has_checkpoint(self, thread_id: str) -> bool:
        """SQLite mirror of :meth:`PostgresCheckpointerAdapter.has_checkpoint`.

        Same root-namespace semantics (``checkpoint_ns = ''`` — the
        LangGraph default for a config carrying only ``thread_id``).

        SQLite has no second connection pool: every other helper in
        this adapter runs on the saver's own connection under the
        saver's lock, so this one does the same. The off-lock win is
        PostgreSQL-only by construction — the SQLite saver is a single
        serialized connection and there is nothing to bypass.
        """
        async with self._saver.lock:
            cursor = await self._saver.conn.execute(
                "SELECT 1 FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ? LIMIT 1",
                (thread_id, ""),
            )
            row = await cursor.fetchone()
            return row is not None

    async def delete_checkpoints_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """Delete checkpoints NOT in keep_ids. Returns deleted count."""
        if not keep_ids:
            return 0

        # Read-cache invalidation seam (SQLite twin — plan §4.3.B):
        # the SQLite saver is NEVER wrapped by the retry proxy, so
        # ``self._saver`` carries no cache and the helper is a no-op.
        # Kept for symmetry: if a proxied SQLite shape ever appears,
        # the raw-SQL bypass hole is already closed here.
        invalidate_cached_reads(self._saver, [thread_id])

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

        # Read-cache invalidation seam (SQLite twin — plan §4.3.B);
        # no-op today, see delete_checkpoints_excluding above.
        invalidate_cached_reads(self._saver, [thread_id])

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
        # Read-cache invalidation seam (SQLite twin — plan §4.3.B);
        # no-op today (the SQLite saver is never proxied).
        invalidate_cached_reads(self._saver, [thread_id])
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

    # ── W1 partial-completion side channel (fix cycle 1) ─────────────
    # Reset at EVERY ``delete_blobs_anti_join`` ENTRY and written at
    # every return; read by the single sanctioned caller
    # (``daemon/services/checkpoint_prune.py::prune_unreferenced_blobs``)
    # immediately after each await in its SERIAL per-pair loop. The
    # public ``(int, int)`` return shape is frozen, so the per-pair
    # stop state (``"retry_exhausted"`` / ``"over_batch_limit"`` /
    # ``None`` + abandoned-chunk count) rides here instead.
    # Class-level defaults keep duck-typed readers defined before the
    # first call; instances shadow them from the first call on.
    last_pair_stop_reason: str | None = None
    last_pair_chunks_skipped: int = 0

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

        MRO-rule note (round 2): because ``.conn`` / ``.lock`` /
        ``setup`` are ABSENT from the pinned ``BaseCheckpointSaver``
        surface, attribute access on the proxy resolves through
        ``__getattr__`` to the wrapped saver — that is how this
        property's consumers (and the readiness probe's topology
        detector) reach the pool. Base-CONCRETE members behave the
        opposite way (explicit forwarders; see the proxy class
        docstring and the K-guard contract test, which is what keeps
        this property safe to hand to LangGraph across base bumps).
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

    async def has_checkpoint(self, thread_id: str) -> bool:
        """Existence probe on the SEPARATE asyncpg pool — NO saver lock.

        Track-1 quick-win (checkpoint read-path plan, item 2). The
        retry-path existence check used to run a FULL
        ``raw_saver.aget(config)``: that (a) took the saver's single
        ``asyncio.Lock`` — serializing every other checkpoint read
        behind it — and (b) deserialized the ENTIRE checkpoint
        (checkpoint + channel values + blobs) only for the caller to
        look at one boolean.

        This probe answers the SAME question with a bounded existence
        query on the adapter's own asyncpg pool (``self._pool`` — the
        pool every other direct-SQL helper in this class already
        uses). It therefore never touches the saver's lock and never
        materialises a payload.

        Semantics are IDENTICAL to the saver lookup it replaces:
        LangGraph's ``aget_tuple`` resolves
        ``config["configurable"].get("checkpoint_ns", "")`` — i.e. the
        ROOT namespace — and reports "no checkpoint" when no
        ``checkpoints`` row matches. The ``checkpoint_ns = ''`` literal
        below IS that root-namespace default (pinned, not bound: this
        probe has exactly one supported namespace).

        NOTE (resource-cost-control charge seam): the saver read path
        charges the DECODED payload to the requesting instance
        (``_charge_checkpoint_read``). This probe decodes no payload,
        so it is deliberately NOT charged — charging a non-payload
        would corrupt the checkpoint read-cost metric.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM checkpoints WHERE thread_id = $1 AND checkpoint_ns = '' LIMIT 1",
                thread_id,
            )
        return row is not None

    async def delete_checkpoints_excluding(
        self, thread_id: str, checkpoint_ns: str, keep_ids: set[str]
    ) -> int:
        """Delete checkpoints NOT in keep_ids. Returns deleted count."""
        if not keep_ids:
            return 0

        # Read-cache invalidation seam 1/4 (plan §4.3.B — raw SQL
        # bypasses the proxy): this DELETE runs on the separate
        # asyncpg pool, so the proxy's write forwarders never see it.
        # ``self._saver`` IS the proxy, so the shared helper reaches
        # its cache; fires BEFORE the DELETE (same before-delegate
        # contract as the proxy writes). No-op while the kill-switch
        # is off (nothing is ever cached).
        invalidate_cached_reads(self._saver, [thread_id])

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
        # Read-cache re-invalidation, seam 1/4 AFTER-half (fix pass F4):
        # the await point between the BEFORE-half and the DELETE COMMIT
        # lets a racing aget_tuple re-put an entry for a row this DELETE
        # then removes — a no-TTL ghost of deleted state. Mirrors the
        # proxy's double protection (invalidate-before + post-write
        # emptiness): the AFTER-half closes the window.
        invalidate_cached_reads(self._saver, [thread_id])
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

        # Read-cache invalidation seam 2/4 (plan §4.3.B) — see the
        # identical note at ``delete_checkpoints_excluding`` above.
        invalidate_cached_reads(self._saver, [thread_id])

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
        # AFTER-half (fix pass F4) — closes the racing re-put window,
        # same as seam 1.
        invalidate_cached_reads(self._saver, [thread_id])
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
        # Read-cache invalidation seam 3/4 (plan §4.3.B — raw SQL
        # bypasses the proxy): same before-delete contract as seams
        # 1/2 above; drops EVERY namespace of the thread.
        invalidate_cached_reads(self._saver, [thread_id])

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
        # AFTER-half (fix pass F4) — the three-statement transaction has
        # THREE await points a racing read could interleave on; the
        # post-commit re-invalidate drops whatever it re-put.
        invalidate_cached_reads(self._saver, [thread_id])

    async def find_excess_checkpoint_groups(
        self, max_per_thread: int
    ) -> list[tuple[str, str, int]]:
        """Find (thread_id, checkpoint_ns, count) groups exceeding max_per_thread."""
        # W1: no sentinel exclusion needed — the readiness-probe sentinel
        # writes checkpoint_writes only and never creates a checkpoints row.
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
        bytes_freed)`` summed across the per-pair LIMIT-batched chunks
        (``DELETE FROM checkpoint_blobs WHERE ctid IN (SELECT ctid
        FROM ... WHERE <anti-join> LIMIT $batch) RETURNING
        OCTET_LENGTH(blob)``; loop until the chunk returns 0 rows).

        The pre-PR4 shape used a single unbounded DELETE per pair —
        ~100k rows per pair worst case (daemon/checkpoint_adapter.py:
        -1376 -- the line range where the unbounded DELETE lived).
        feature/blob-prune-default-on promotes destructive-on to the
        unconditional default; the very first production run after the
        promote faces the accumulated pair-excess backlog. Bounding
        the per-pair DELETE into chunks of
        ``CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE`` (default 1000)
        rows prevents a single statement from holding an asyncpg pool
        connection for the entire per-pair payload (pool-hazard class).
        Each chunk is its own SERIALIZABLE transaction with bounded
        retry on SQLSTATE 40001 (serialization_failure) / 40P01
        (deadlock_detected) — see the wrap rationale block at the
        method body. Retry budget per chunk:
        ``CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES`` (daemon/constants.py);
        on exhaustion the chunk is SKIPPED with an ERROR log and the
        outer loop EXITS with the partial totals accumulated so far
        (the next cycle retries whatever's left — never raises into
        the maintenance loop, Operation E's never-raise contract).

        PARTIAL-COMPLETION VISIBILITY (W1, fix cycle 1): an
        early-stopped pair (``"retry_exhausted"`` / LIMIT-guard
        ``"over_batch_limit"``) no longer reads as ordinary
        completion — the stop reason + abandoned-chunk count are
        published on the instance (``last_pair_stop_reason`` /
        ``last_pair_chunks_skipped``, reset every call) for the
        sanctioned caller to thread into ``BlobPruneSummary`` and the
        ``maintenance_runs`` audit row. The
        ``(deleted_count, bytes_freed)`` return remains the REAL
        partial totals.

        DANGER — data-destroying. The only sanctioned call site is
        behind the ``if not destructive: ... continue`` guard in
        ``daemon/services/checkpoint_prune.py::prune_unreferenced_blobs``;
        with ``destructive=False`` (manual preview path) no call path
        reaches this method. The dual-arm env-flag gate is gone —
        see feature/blob-prune-default-on.
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
        #
        # ── LIMIT-batching rationale (feature/blob-prune-default-on) ──
        #
        # POOL-HAZARD class: with the dual-arm env gate retired
        # (destructive unconditional), the very first production prune
        # after promote faces the accumulated pair-excess backlog
        # (~100k refs per pair worst case at the old unbounded-D DELETE
        # call site). A single statement of that size holds an asyncpg
        # pool connection for the entire per-pair payload and exposes
        # the maintenance window to pool exhaustion across the rest of
        # the maintenance loop. The chunk loop here bounds each
        # DELETE statement to ``CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE``
        # rows (default 1000) — each chunk is its own SERIALIZABLE
        # transaction so the retry wrap re-acquires a fresh connection
        # per chunk AND the per-chunk row count is small enough to
        # classify SSI aborts cheaply. The chunk predicate uses
        # ``ctid IN (SELECT ctid FROM checkpoint_blobs WHERE
        # <anti-join> LIMIT $batch)`` — ``ctid`` is the PG physical
        # row id; it is stable within a transaction and does not need
        # a surrogate ``id`` column on the composite-PK table. The
        # chunk loop exits as soon as one chunk returns 0 rows
        # (full pair exhausted) and propagates a non-isolation-family
        # exception by re-raising (the outer per-pair catch in
        # checkpoint_prune.py converts it to a SKIP — the
        # never-raise contract lives there, not here).
        total_deleted = 0
        total_bytes_freed = 0
        # W1 (fix cycle 1): reset the partial-completion side channel
        # at ENTRY — a mid-loop raise (non-isolation family) must not
        # leave the PREVIOUS pair's stop reason readable for the next
        # call's caught-and-skipped pair (misattribution). The write
        # site after the loop publishes the reason on normal returns.
        self.last_pair_stop_reason = None
        self.last_pair_chunks_skipped = 0
        # Read-cache invalidation seam 4/4 (plan §4.3.B): the anti-join
        # DELETE runs on the asyncpg pool, bypassing the proxy's write
        # forwarders. A referenced blob cannot be deleted by
        # construction, so a cached tuple stays VALID — but the
        # reference snapshot is taken under this transaction's
        # isolation, and a racing aput (READ COMMITTED, no SSI edge)
        # can slip a new blob past it. Conservative invalidation is
        # cheap (one thread's keys) and matches the plan's complete
        # invalidation set. No-op while the kill-switch is off.
        invalidate_cached_reads(self._saver, [thread_id])
        # W1 (fix cycle 1): partial-completion accounting. A chunk that
        # comes back with a stop reason (retry exhaustion) or the
        # LIMIT-guard break below both leave residual rows — the pair
        # must NOT read as ordinary completion. The stop state rides
        # the instance side channel (see the write site below); the
        # public ``(int, int)`` return shape stays frozen.
        chunks_skipped = 0
        stop_reason: str | None = None
        while True:
            rows, chunk_stop = await self._delete_blobs_anti_join_chunk(
                thread_id, checkpoint_ns, CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE
            )
            if chunk_stop is not None:
                # The chunk primitive abandoned this chunk with ZERO
                # rows deleted (retry exhaustion). NOT natural pair
                # exhaustion — record why the loop stopped, keep the
                # accumulated partial totals, and stop THIS pair's
                # loop (residual rows stay for the next maintenance
                # cycle; never raises — same conversion philosophy as
                # the per-pair catch in checkpoint_prune.py).
                chunks_skipped += 1
                stop_reason = chunk_stop
                break
            chunk_count = len(rows)
            if chunk_count == 0:
                break
            total_deleted += chunk_count
            total_bytes_freed += sum(
                int(r["n"]) for r in rows if r["n"] is not None
            )
            # Chunk-size sanity guard — the per-statement upper bound
            # must hold. asyncpg cannot refuse the LIMIT clause, but a
            # future refactor that drops it would silently grow the
            # per-statement footprint back into the pool-hazard class.
            # Explicit branch, NOT an ``assert`` — asserts vanish under
            # ``python -O`` and this bound must survive optimized
            # interpreters. On violation: log ERROR and stop THIS
            # pair's chunk loop (skip-rest-of-pair is the safe
            # direction — residual rows stay for the next maintenance
            # cycle; never raises, same conversion philosophy as the
            # per-pair catch in checkpoint_prune.py). W1 (fix cycle 1):
            # the break now also records ``over_batch_limit`` so the
            # partial pair is visible in the audit summary instead of
            # reading as ordinary completion.
            if chunk_count > CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE:
                logger.error(
                    "chunk DELETE returned %d rows > batch size %d — "
                    "LIMIT violated, pool-hazard class reopened; "
                    "stopping this pair's chunk loop (residual rows "
                    "stay for the next maintenance cycle)",
                    chunk_count,
                    CHECKPOINT_BLOB_PRUNE_DELETE_BATCH_SIZE,
                )
                stop_reason = "over_batch_limit"
                break
        # W1 (fix cycle 1): publish the per-pair stop state for the
        # single sanctioned caller
        # (``daemon/services/checkpoint_prune.py::prune_unreferenced_blobs``).
        # The public return shape is FROZEN (ABC contract + SQLite
        # no-op sibling + the ``test_no_env_flag_remnants`` signature
        # pin), so the signal rides instance attributes: reset at
        # EVERY call entry (below) and read immediately after each
        # await in the caller's SERIAL per-pair loop — no cross-pair
        # staleness is possible on the sanctioned path.
        self.last_pair_stop_reason = stop_reason
        self.last_pair_chunks_skipped = chunks_skipped
        # Read-cache re-invalidation, seam 4/4 AFTER-half (fix pass F4):
        # the chunk loop's awaits span the whole pair deletion — a racing
        # aget_tuple could have re-put the thread's entry mid-loop; the
        # post-loop invalidate closes the window (the BEFORE-half at
        # entry covered only the loop's start).
        invalidate_cached_reads(self._saver, [thread_id])
        return (total_deleted, total_bytes_freed)

    async def _delete_blobs_anti_join_chunk(
        self, thread_id: str, checkpoint_ns: str, batch_size: int
    ) -> tuple[list[Any], str | None]:
        """One LIMIT-bounded chunk of the destructive anti-join DELETE.

        Returns ``(rows, stop_reason)`` — the ``OCTET_LENGTH(blob)``
        rows of the chunk (0..N) plus a stop reason when this chunk was
        abandoned BEFORE any of its rows deleted: ``"retry_exhausted"``
        on isolation-family retry exhaustion, ``None`` on a normal
        return (including the natural empty chunk that ends the pair).
        The outer caller (:meth:`delete_blobs_anti_join`) loops until a
        chunk returns zero rows with no stop reason; the per-chunk
        SERIALIZABLE wrap + bounded retry on SQLSTATE 40001 / 40P01
        lives here so the retry budget is per-chunk, not per-pair (a
        chunk that hits a retryable conflict yields its connection and
        re-enters on a fresh snapshot — the pair loop re-issues with
        the same predicate on a possibly-shrunk candidate set).

        On isolation-family retry exhaustion: logs ERROR, returns
        ``([], "retry_exhausted")`` — the outer loop exits with
        whatever it already accumulated AND a recorded stop reason
        (surfaced as a partial-completion signal by the caller), never
        raising into the maintenance loop.
        """
        retries_left = CHECKPOINT_BLOB_PRUNE_DELETE_RETRIES
        attempt = 0
        while True:
            attempt += 1
            try:
                # Same connection discipline as adelete_thread: acquire a
                # pool connection per attempt (a fresh connection ⇒ fresh
                # snapshot on retry) and let asyncpg's transaction context
                # issue BEGIN ISOLATION LEVEL SERIALIZABLE … COMMIT/ROLLBACK.
                #
                # LIMIT-batching (feature/blob-prune-default-on): the
                # chunk SELECT picks at most ``batch_size`` ctids matching
                # the anti-join predicate; the DELETE only touches those
                # ctids in the same transaction. Pool-hazard class is
                # closed by construction — the per-statement footprint is
                # bounded regardless of how much pair-excess backlog the
                # first post-promote cycle walks into. ``ctid`` is the PG
                # physical row id; stable within the same transaction and
                # usable as a DELETE filter without a surrogate ``id``
                # column on the composite-PK table.
                async with self._pool.acquire() as conn:
                    async with conn.transaction(isolation="serializable"):
                        rows = await conn.fetch(
                            "DELETE FROM checkpoint_blobs b WHERE b.ctid IN ("
                            # The subquery SELF-ALIASES as ``b`` — the SAME
                            # alias ``_BLOB_ANTI_JOIN_PREDICATE`` is written
                            # against. Inside the subquery the inner ``b``
                            # shadows the outer DELETE alias, so the
                            # predicate binds to the chunk candidates.
                            # (Review C1: a prior revision declared ``b2``
                            # here — the predicate's ``b`` then resolved to
                            # the OUTER DELETE alias, ``b2`` stayed
                            # unconstrained, and the subquery returned the
                            # first ``batch`` ctids of the WHOLE table in
                            # arbitrary scan order: per-pair exhaustion
                            # silently never completed on production-scale
                            # tables.)
                            "SELECT b.ctid FROM checkpoint_blobs b WHERE"
                            + _BLOB_ANTI_JOIN_PREDICATE
                            + " LIMIT $3"
                            ") RETURNING OCTET_LENGTH(blob) AS n",
                            thread_id,
                            checkpoint_ns,
                            batch_size,
                        )
                return list(rows), None
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
                    # Exhausted: SKIP the chunk — zero rows deleted for
                    # this chunk, never raise. Skipping is the SAFE
                    # direction (blobs survive; the next maintenance
                    # cycle retries whatever is left).
                    logger.error(
                        "[CheckpointPerf] blob_prune "
                        "SERIALIZABLE_RETRY_EXHAUSTED thread=%s ns=%s "
                        "batch=%d — anti-join DELETE chunk aborted with "
                        "SQLSTATE %s on all %d attempts; skipping chunk, "
                        "zero rows deleted (safe direction; next "
                        "maintenance cycle retries)",
                        thread_id[:8],
                        checkpoint_ns,
                        batch_size,
                        sqlstate,
                        attempt,
                    )
                    return [], "retry_exhausted"
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
