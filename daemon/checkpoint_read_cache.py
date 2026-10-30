"""Checkpoint read-through cache — the PR-1 SUBSTRATE (plan §4, default OFF).

A write-invalidating, per-thread LRU read cache living inside the saver
retry proxy's read path (``daemon/checkpoint_adapter.py`` —
``_SaverRetryProxy.aget`` / ``aget_tuple``). The purpose (plan §4):
collapse the repeated checkpoint reads of one dispatch window onto one
cold read per ``(thread_id, checkpoint_ns)`` and remove the decode cost
of repeat reads. The DESK-CLEARING constraint of PR-1: the cache ships
**INERT** — ``CHECKPOINT_READ_CACHE_ENABLED`` defaults to OFF, every
read takes the miss path (identical to today), invalidation hooks fire
as no-ops, and the hit/miss counters still emit (all misses). The
dispatch-window lifecycle (pop/clear in ``instance_messaging.py``) is
PR-2 and is NOT built here.

Placement (plan §4.1): ABOVE ``_call_with_retry`` and ABOVE
``_charge_checkpoint_read`` — the cache decides whether the saver is
consulted at all, and a HIT still passes the returned payload through
the charge seam (never silently uncharged — an avoided DB read is a
different fact from an uncharged read).

Keying (plan §4.2): ``(thread_id, checkpoint_ns)``. Sufficient because
every write invalidates — no version belongs in the key; a hit is by
construction the latest committed state for that pair.

Copy policy (plan §4.2): SHALLOW COPY PER HIT — a new
``CheckpointTuple`` container, new ``config`` / ``metadata`` /
``pending_writes`` containers, a new ``channel_values`` dict and a new
``messages`` list, with SHARED message objects. A deep copy would erase
the win (301 KB–2.9 MB per hit). The stored artifact is itself a
shallow copy taken at PUT time, so the object handed to the miss-path
caller is never the cached object either.

Bounds (plan §4.7): LRU cap 32 entries AND a 64 MB byte budget,
evicting oldest-first. The plan's live-entry estimate is ~7–8 (worker
pool 5 + chat workers 2 + manager lane), ~2.4 MB typical — the bounds
are the pathological guard.

Concurrency (plan §4.8): single process, single event loop; reads
within a turn are sequential. A ``threading.Lock`` guards the (small)
accounting state anyway so the proxy's SYNC-twin forwarders can never
interleave a corrupt eviction from another thread.

This module is stdlib + ``bounded_deep_size`` only — no imports from
``daemon.checkpoint_adapter`` (no cycle; the adapter imports THIS).
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from typing import Any, Iterable, Optional

from daemon.services.resource_monitor.charge import bounded_deep_size

logger = logging.getLogger(__name__)


# ── Kill-switch (plan §4.5) ──────────────────────────────────────────────────

#: Env var gating the read cache. DEFAULT OFF in PR-1 (the substrate
#: ships inert); PR-5 flips the default after staging acceptance.
#: Truthy spellings: 1/true/yes/on (case-insensitive). Everything else
#: — including UNSET — is OFF.
CHECKPOINT_READ_CACHE_ENABLED_ENV = "CHECKPOINT_READ_CACHE_ENABLED"


def checkpoint_read_cache_enabled() -> bool:
    """Env-first gate — ``CHECKPOINT_READ_CACHE_ENABLED``, default OFF.

    OFF is OBSERVABLE, not silent: the proxy read forwarders still run
    the key extraction and increment the miss counter on every read,
    and every invalidation hook still fires (as a no-op) — a disabled
    cache is visible in ``ensemble_checkpoint_cache_*_total`` (all
    misses), per plan §4.5.
    """
    raw = os.environ.get(CHECKPOINT_READ_CACHE_ENABLED_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# ── Bounds (plan §4.7) ───────────────────────────────────────────────────────

#: LRU entry cap — 32 entries ≈ 4× the plan's ~7–8 live-entry estimate
#: (worker pool 5 + chat workers 2 + manager lane).
CHECKPOINT_READ_CACHE_MAX_ENTRIES = 32

#: Byte budget — 64 MB, ≈ 2.8× the plan's ~23 MB pathological estimate.
CHECKPOINT_READ_CACHE_BYTE_BUDGET_BYTES = 64 * 1024 * 1024


# ── Shallow copy (plan §4.2) ─────────────────────────────────────────────────


def _copy_config_mapping(value: Any) -> Any:
    """Shallow-copy a config-shaped mapping; share everything else."""
    if isinstance(value, dict):
        return dict(value)
    return value


def shallow_copy_checkpoint_tuple(value: Any) -> Any:
    """One shallow copy of a ``CheckpointTuple``-shaped artifact.

    Copies the CONTAINERS — the tuple itself, ``config`` /
    ``metadata`` / ``parent_config`` / ``pending_writes`` mappings and
    lists, the ``checkpoint`` dict, its ``channel_values`` dict, and
    the ``messages`` list inside it — and SHARES the message objects
    (a deep copy would erase the win; the mutation audit in plan §4.2
    shows every consumer is read-only on the shared objects).

    NEVER raises for a foreign shape: anything that does not quack
    like a ``CheckpointTuple`` is returned AS-IS (the cache's copy
    discipline degrades to object sharing rather than breaking the
    read). ``None`` stays ``None``.
    """
    if value is None:
        return None
    try:
        config = _copy_config_mapping(getattr(value, "config", None))
        checkpoint = getattr(value, "checkpoint", None)
        if isinstance(checkpoint, dict):
            checkpoint = dict(checkpoint)
            channel_values = checkpoint.get("channel_values")
            if isinstance(channel_values, dict):
                channel_values = dict(channel_values)
                messages = channel_values.get("messages")
                if isinstance(messages, list):
                    channel_values["messages"] = list(messages)
                checkpoint["channel_values"] = channel_values
        metadata = _copy_config_mapping(getattr(value, "metadata", None))
        parent_config = getattr(value, "parent_config", None)
        parent_config = (
            _copy_config_mapping(parent_config) if parent_config is not None else None
        )
        pending_writes = getattr(value, "pending_writes", None)
        if isinstance(pending_writes, list):
            pending_writes = list(pending_writes)
        value_type = type(value)
        return value_type(
            config=config,
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )
    except Exception:  # noqa: BLE001 — a foreign shape shares, never breaks
        return value


# ── The cache ────────────────────────────────────────────────────────────────


class CheckpointReadCache:
    """LRU + byte-budget cache of post-deserialize ``CheckpointTuple``s.

    Keyed ``(thread_id, checkpoint_ns)`` (plan §4.2 — sufficient
    because every write invalidates). Values are (shallow-copied
    tuple, size) pairs; ``get`` returns a FRESH shallow copy per hit
    and refreshes LRU recency. ``put`` stores a shallow copy so the
    miss-path caller keeps the original object privately.

    All sizes come from ``bounded_deep_size`` — the SAME estimator the
    charge seam uses, so the byte budget measures what a hit would
    have walked anyway. The put-side walk reuses a PRE-COMPUTED size
    when the caller already walked the payload (M-2 discipline: one
    walk per materialization).
    """

    __slots__ = ("_entries", "_total_bytes", "_lock")

    def __init__(self) -> None:
        # (thread_id, checkpoint_ns) -> (tuple_copy, size_bytes); the
        # OrderedDict order IS the LRU order (oldest first).
        self._entries: "OrderedDict[tuple[str, str], tuple[Any, int]]" = OrderedDict()
        self._total_bytes = 0
        self._lock = threading.Lock()

    # ── reads ────────────────────────────────────────────────────────────

    def get(self, thread_id: str, checkpoint_ns: str) -> Optional[tuple[Any, int]]:
        """Return ``(fresh shallow copy, stored size)`` of the cached
        tuple, or None.

        A hit refreshes LRU recency. The returned copy shares message
        objects with the stored artifact (plan §4.2 copy policy). The
        stored size is handed back WITH the copy so the proxy's HIT
        path can charge the exact same byte count a real read would
        have walked — no second ``bounded_deep_size`` walk on a hit
        (the M-2 one-walk discipline extended to the cache).
        """
        key = (thread_id, checkpoint_ns)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            stored, size = entry
        # The shallow copy runs OUTSIDE the lock (container-only work;
        # the stored artifact is never mutated by it).
        return shallow_copy_checkpoint_tuple(stored), size

    def thread_cached(self, thread_id: str) -> bool:
        """Is ANY namespace of this thread currently cached?"""
        with self._lock:
            return any(k[0] == thread_id for k in self._entries)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    # ── writes ───────────────────────────────────────────────────────────

    def put(
        self,
        thread_id: str,
        checkpoint_ns: str,
        value: Any,
        *,
        size: Optional[int] = None,
    ) -> None:
        """Store a shallow copy of ``value`` under ``(thread_id, ns)``.

        ``size`` — the pre-computed ``bounded_deep_size`` of ``value``
        when the caller already walked it; ``None`` walks here (once).
        A failed walk stores size 0 (the entry still caches; the byte
        budget merely under-counts it — fail-open, the cache is an
        optimization, not accounting).
        """
        if value is None:
            return
        if size is None:
            try:
                size, _nodes, _partial = bounded_deep_size(value)
            except Exception:  # noqa: BLE001 — fail-open (plan R1)
                size = 0
        size = max(0, int(size))
        stored = shallow_copy_checkpoint_tuple(value)
        key = (thread_id, checkpoint_ns)
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._total_bytes -= old[1]
            self._entries[key] = (stored, size)
            self._total_bytes += size
            self._evict_locked()

    def _evict_locked(self) -> None:
        """Enforce BOTH bounds, evicting oldest-first. Caller holds the lock."""
        while len(self._entries) > CHECKPOINT_READ_CACHE_MAX_ENTRIES:
            self._evict_oldest_locked()
        while (
            self._total_bytes > CHECKPOINT_READ_CACHE_BYTE_BUDGET_BYTES
            and len(self._entries) > 1
        ):
            # ``> 1`` — never evict the JUST-INSERTED entry even if it
            # alone exceeds the budget (one oversized artifact stays
            # cached; the bound is a pathological guard, not a
            # hard wall — plan §4.7 sizing).
            self._evict_oldest_locked()

    def _evict_oldest_locked(self) -> None:
        key, entry = self._entries.popitem(last=False)
        self._total_bytes -= entry[1]
        if self._total_bytes < 0:  # defensive: never let accounting drift
            self._total_bytes = 0

    # ── invalidation (plan §4.3) ─────────────────────────────────────────

    def invalidate(self, thread_id: str) -> None:
        """Drop EVERY namespace of ``thread_id`` — fires BEFORE the write."""
        with self._lock:
            doomed = [k for k in self._entries if k[0] == thread_id]
            for key in doomed:
                _stored, size = self._entries.pop(key)
                self._total_bytes -= size

    def invalidate_many(self, thread_ids: Iterable[str]) -> None:
        for thread_id in thread_ids:
            self.invalidate(thread_id)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._total_bytes = 0


def extract_cache_key(args: tuple, kwargs: dict) -> Optional[tuple[str, str]]:
    """Extract ``(thread_id, checkpoint_ns)`` from an aget-family call.

    Mirrors the charge seam's extraction (``_charge_instance_from_args``
    in ``daemon/checkpoint_adapter.py``): the runnable config is the
    first positional arg (or ``config=`` kwarg); thread_id and
    checkpoint_ns live under ``config["configurable"]``. Returns None
    for an un-attributable call (no config / no thread_id) — such
    reads bypass the cache entirely, never guessed.

    VERSION-SCOPED READS ARE NEVER CACHEABLE (read-cache PR-1 fix pass
    F1): when ``configurable`` carries a TRUTHY ``checkpoint_id``, the
    read is pinned to that specific checkpoint version (the pinned
    saver's SELECT honors it). Such a read must neither CONSULT nor
    POPULATE the cache: the cache key is ``(thread_id, ns)`` with
    LATEST-commit semantics (every write invalidates ⇒ a hit is the
    latest committed state by construction) — a version-scoped result
    stored under the thread key would make a later latest-semantics
    hit serve the WRONG VERSION. Returns None so the caller takes the
    real saver path and skips the put. (A FALSY ``checkpoint_id`` —
    None or "" — is the latest-semantics spelling and stays cacheable,
    matching the upstream reader's own ``is not None`` gate.)
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
    if not isinstance(thread_id, str) or not thread_id:
        return None
    if configurable.get("checkpoint_id"):
        return None  # version-scoped read — never consult, never populate
    checkpoint_ns = configurable.get("checkpoint_ns", "")
    if not isinstance(checkpoint_ns, str):
        checkpoint_ns = ""
    return (thread_id, checkpoint_ns)


def extract_thread_ids_from_write(
    method_name: str, args: tuple, kwargs: dict
) -> list[str]:
    """Extract EVERY thread a WRITE call may mutate — best-effort.

    GENERIC COLLECTOR (one rule covers the whole write family):

    * a POSITIONAL ``str`` argument is a candidate thread id — this is
      exactly the shape of ``adelete_thread(thread_id)`` /
      ``delete_thread(thread_id)`` / ``copy_thread(from_thread, ...)``;
      a stray str that is NOT a thread id (e.g. an op name) only ever
      invalidates a key that was never cached — a harmless no-op;
    * a POSITIONAL ``dict`` argument is probed for
      ``["configurable"]["thread_id"]`` — the runnable-config shape of
      ``aput`` / ``aput_writes`` / ``adelete_for_runs`` / sync twins
      (the same extraction the charge seam pins);
    * the ``thread_id=`` and ``config=`` kwargs get the same probes.

    Returns a deduped list. EMPTY = un-attributable — the caller's
    DEGRADED SAFETY response is a FULL cache clear (a write we cannot
    attribute may have mutated any cached thread; stale-is-worse-than-
    cold — plan §4.3's completeness requirement).
    """
    candidates: list[str] = []

    def _probe(value: Any) -> None:
        if isinstance(value, str) and value:
            candidates.append(value)
        elif isinstance(value, dict):
            configurable = value.get("configurable")
            if isinstance(configurable, dict):
                thread_id = configurable.get("thread_id")
                if isinstance(thread_id, str) and thread_id:
                    candidates.append(thread_id)

    for positional in args:
        _probe(positional)
    if "thread_id" in kwargs:
        _probe(kwargs["thread_id"])
    if "config" in kwargs:
        _probe(kwargs["config"])
    # Dedupe, order-preserving.
    return list(dict.fromkeys(candidates))


def invalidate_cached_reads(saver: Any, thread_ids: Iterable[str]) -> None:
    """INVALIDATION_SEAMS helper — the one sanctioned invalidation route.

    Used by BOTH invalidation families (plan §4.3): the proxy's write
    forwarders (via ``self._read_cache``) and the adapter's four
    raw-SQL DELETE sites that bypass the proxy (they hold the proxy as
    ``self._saver``). A bare saver (the SQLite adapter's, tests')
    carries no ``_read_cache`` attribute — the call is then a NO-OP by
    design (nothing is cached on that install shape).

    FAIL-OPEN: an invalidation failure is logged, never raised — it
    must not break a production write path (the post-write emptiness
    self-check is the backstop signal).
    """
    try:
        cache = getattr(saver, "_read_cache", None)
        if cache is None:
            return
        ids = [t for t in thread_ids if t]
        if ids:
            cache.invalidate_many(ids)
    except Exception:  # noqa: BLE001 — fail-open; self-check backstops
        logger.warning(
            "[CheckpointReadCache] invalidation failed — the post-write "
            "emptiness self-check will surface any resulting staleness",
            exc_info=True,
        )
