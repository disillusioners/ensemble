"""Per-instance charge registry — P2 T1 (charge-seam attribution core).

Hybrid-C attribution (architecture-recommendation §2.2): cheap charge
seams are ALWAYS-ON while the monitor is enabled, feeding per-instance
``charged_bytes`` counters that the sampler samples each tick and the
deep scan (``deep_scan.py``) periodically reconciles against a bounded
``sys.getsizeof`` walk of the live graph.

Design contract (phase2-plan T1/D2/D14, R1):

* **Hot-path cost** — the seam calls are a single ``enabled`` bool check
  when the monitor is disabled (identity no-ops, ZERO dict growth), and
  an int add on an existing per-instance record when enabled. The
  sizing walk itself is bounded (node cap + depth cap + id-memoisation).
* **Thread-safety** — the seams are event-loop-affine (all charge WRITES
  originate on the daemon event loop), so the write path is lock-free;
  a ``threading.Lock`` guards cross-thread READS (sampler snapshots)
  and the one cross-thread WRITE (``set_deep_bytes`` from the deep
  scan, which runs on the sampler thread). Field-level writes never
  overlap between the two writer threads, so no lost updates.
* **D14 no-raw-args guard** — this module persists SCALARS ONLY:
  per-instance byte counters keyed by ``instance_id``. No message
  content, no tool args, no prompts, nothing that could carry secret
  plaintext. The sizing walk measures bytes and discards the objects.
* **Zero runtime deps** — stdlib only, so the hot-path seam modules
  (``instance_messaging``, ``checkpoint_adapter``) can import it with
  no import-cycle or heavyweight-dependency risk.

Public API (consumed by the integration coder — sampler/facade lane):

  Wiring:        ``charge.set_enabled(True)`` at monitor boot,
                 ``charge.set_enabled(False)`` at shutdown.
  Seams:         ``note_message_append(instance_id, obj)``
                 ``note_message_replace(instance_id, new_obj, old_obj)``
                 ``note_checkpoint_read(instance_id, payload)``
  Deep scan:     ``set_deep_bytes(instance_id, deep_bytes, partial_walk=)``
                 (+ the shared ``bounded_deep_size`` walker)
  Sampling:      ``snapshot_instance(iid)`` / ``snapshot_all()`` /
                 ``aggregate()`` / ``register(iid)`` / ``reset(iid=None)``

All of the above exist BOTH as module-level functions (delegating to a
process-wide singleton — what the seams and the sampler use) and as
methods on :class:`ChargeRegistry` (what tests use for isolation).
"""

from __future__ import annotations

import collections.abc as _abc
import logging
import os
import sys
import threading
import types as _types
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Bounds (env-overridable; read ONCE at import — hot path never
#    touches os.environ) ─────────────────────────────────────────────
# Charge seams (message append/replace + checkpoint reads): small caps,
# these walks fire on the streaming hot path.
DEFAULT_CHARGE_NODE_CAP = int(
    os.environ.get("RESOURCE_MONITOR_CHARGE_NODE_CAP", "4096")
)
DEFAULT_CHARGE_DEPTH_CAP = int(
    os.environ.get("RESOURCE_MONITOR_CHARGE_DEPTH_CAP", "16")
)
# Deep scan (1 instance/tick reconcile): roomier caps, off the hot path.
DEFAULT_DEEP_SCAN_NODE_CAP = int(
    os.environ.get("RESOURCE_MONITOR_DEEP_SCAN_NODE_CAP", "20000")
)
DEFAULT_DEEP_SCAN_DEPTH_CAP = int(
    os.environ.get("RESOURCE_MONITOR_DEEP_SCAN_DEPTH_CAP", "24")
)
# Registry cardinality bound: one record per distinct instance_id ever
# charged while enabled. Oldest-touched records are evicted LRU-style
# beyond the cap (active instances are re-touched on every charge, so
# they survive; a daemon would need 8k+ live instance churn in one
# boot to ever hit this).
DEFAULT_MAX_RECORDS = int(
    os.environ.get("RESOURCE_MONITOR_CHARGE_MAX_RECORDS", "8192")
)


# ── Bounded deep-size walker (shared by charge seams + deep scan) ────

# Leaves whose getsizeof already covers their full payload — counted,
# never descended (descending a str/bytes would count every char).
_ATOMIC_LEAVES = (
    str,
    bytes,
    bytearray,
    memoryview,
    int,
    float,
    complex,
    bool,
    type(None),
    range,
)

# Objects we measure but NEVER descend into — their __dict__ is either
# engine machinery (modules, functions, code objects) or would drag in
# unbounded foreign state (frames, tracebacks).
_NO_DESCEND = (
    type,
    _types.ModuleType,
    _types.FunctionType,
    _types.BuiltinFunctionType,
    _types.MethodType,
    _types.LambdaType,
    _types.TracebackType,
    _types.FrameType,
    _types.CodeType,
)


def bounded_deep_size(
    obj: Any,
    *,
    node_cap: Optional[int] = None,
    depth_cap: Optional[int] = None,
) -> tuple[int, int, bool]:
    """Bounded memoised ``sys.getsizeof`` walk. Returns
    ``(total_bytes, nodes_visited, partial)``.

    * ``node_cap`` — hard bound on nodes visited; exceeded ⇒
      ``partial=True`` (the walk stops, the estimate undercounts).
    * ``depth_cap`` — descent depth bound; a CONTAINER skipped at the
      depth cap sets ``partial=True`` (scalar leaves never do).
    * id-memoisation — an object reachable through several paths is
      counted exactly once (shared substructure, cycles).
    """
    if node_cap is None:
        node_cap = DEFAULT_CHARGE_NODE_CAP
    if depth_cap is None:
        depth_cap = DEFAULT_CHARGE_DEPTH_CAP
    total = 0
    nodes = 0
    partial = False
    seen: set[int] = set()
    stack: list[tuple[Any, int]] = [(obj, 0)]
    while stack:
        cur, depth = stack.pop()
        oid = id(cur)
        if oid in seen:
            continue
        seen.add(oid)
        if nodes >= node_cap:
            partial = True
            break
        nodes += 1
        total += sys.getsizeof(cur)
        if isinstance(cur, _ATOMIC_LEAVES):
            continue  # payload fully covered by getsizeof
        if isinstance(cur, _NO_DESCEND):
            continue
        if depth >= depth_cap:
            partial = True  # container measured, contents skipped
            continue
        child_depth = depth + 1
        try:
            if isinstance(cur, dict):
                stack.extend((k, child_depth) for k in cur.keys())
                stack.extend((v, child_depth) for v in cur.values())
            elif isinstance(cur, _abc.Mapping):
                stack.extend((k, child_depth) for k in cur.keys())
                stack.extend((v, child_depth) for v in cur.values())
            elif isinstance(cur, (list, tuple, _abc.Sequence)):
                stack.extend((v, child_depth) for v in cur)
            elif isinstance(cur, (set, frozenset, _abc.Set)):
                stack.extend((v, child_depth) for v in cur)
            else:
                # Arbitrary instance: descend into its attribute dict
                # when present and non-empty (pydantic models, LangChain
                # messages, dataclasses, plain objects). Slotted-only
                # objects are measured (getsizeof) but not descended —
                # an accepted estimate gap (§2.2: 40–80 % coverage).
                d = getattr(cur, "__dict__", None)
                if isinstance(d, dict) and d:
                    stack.append((d, child_depth))
        except Exception:  # mutated mid-iteration / hostile __iter__
            partial = True
    return total, nodes, partial


# ── Per-instance record ──────────────────────────────────────────────


class InstanceChargeRecord:
    """Scalar-only per-instance counters (D14)."""

    __slots__ = (
        "instance_id",
        "message_bytes",
        "checkpoint_bytes",
        "all_state_messages_len",
        "deep_bytes",
        "deep_partial",
        "deep_scans",
        "seq",
    )

    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id
        self.message_bytes = 0
        self.checkpoint_bytes = 0
        # Cheap structural count (P2 T5): number of message APPENDS
        # observed at the instance_messaging append/replace seam since
        # the record was created. Replace keeps list length (never
        # re-counted); compaction removals have no seam, so this is a
        # monotone cheap estimate of the in-flight state-message list
        # length — never a deep walk (D5). Covered by the row's
        # ``estimated`` honesty labeling (D13).
        self.all_state_messages_len = 0
        self.deep_bytes = 0
        self.deep_partial = False
        self.deep_scans = 0
        self.seq = 0

    @property
    def charged_bytes(self) -> int:
        """Total charge-seam bytes (messages + checkpoint reads)."""
        return self.message_bytes + self.checkpoint_bytes

    def as_dict(self) -> dict:
        """Scalar snapshot — the ONLY shape that leaves this module."""
        return {
            "instance_id": self.instance_id,
            "message_bytes": self.message_bytes,
            "checkpoint_bytes": self.checkpoint_bytes,
            "charged_bytes": self.charged_bytes,
            "all_state_messages_len": self.all_state_messages_len,
            "deep_bytes": self.deep_bytes,
            "deep_partial": self.deep_partial,
            "deep_scans": self.deep_scans,
        }


# ── Registry ─────────────────────────────────────────────────────────


class ChargeRegistry:
    """Per-instance counter registry.

    Write path (seams, event-loop-affine): lock-free int adds on an
    existing record; the lock is taken ONLY on first-sighting record
    creation (once per instance) and for LRU eviction.

    Read path (sampler thread): locked, consistent-enough snapshots.
    """

    def __init__(self, *, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, InstanceChargeRecord] = {}
        self._max_records = max(1, int(max_records))
        self._seq = 0
        self._enabled = False

    # ── enabled ──
    def set_enabled(self, enabled: bool) -> None:
        """Master switch. ``False`` ⇒ every seam is an identity no-op
        and the registry neither reads nor grows (T1/AC1)."""
        self._enabled = bool(enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── write path ──
    def _get_or_create(self, instance_id: str) -> Optional[InstanceChargeRecord]:
        """Touch + return the record for ``instance_id``.

        Fast path: enabled + known record ⇒ no lock, no allocation
        (one dict lookup, one int bump). Slow path: first sighting ⇒
        locked create with LRU-evict-at-cap.
        """
        if not self._enabled or not instance_id:
            return None
        rec = self._records.get(instance_id)
        if rec is not None:
            self._seq += 1
            rec.seq = self._seq
            return rec
        with self._lock:
            rec = self._records.get(instance_id)
            if rec is None:
                while len(self._records) >= self._max_records:
                    self._evict_one_locked()
                rec = InstanceChargeRecord(instance_id)
                self._records[instance_id] = rec
            self._seq += 1
            rec.seq = self._seq
            return rec

    def _evict_one_locked(self) -> None:
        """Evict the least-recently-touched record. Caller holds lock."""
        if not self._records:
            return
        victim_id = None
        victim_seq = None
        for iid, rec in self._records.items():
            if victim_seq is None or rec.seq < victim_seq:
                victim_id, victim_seq = iid, rec.seq
        if victim_id is not None:
            del self._records[victim_id]

    def add_message_bytes(self, instance_id: str, n: int) -> None:
        """Add a (possibly negative — replace-delta) message byte count."""
        rec = self._get_or_create(instance_id)
        if rec is not None:
            rec.message_bytes += int(n)

    def add_checkpoint_bytes(self, instance_id: str, n: int) -> None:
        rec = self._get_or_create(instance_id)
        if rec is not None:
            rec.checkpoint_bytes += int(n)

    def increment_message_count(self, instance_id: str) -> None:
        """Cheap append counter for the T2 ``all_state_messages_len``
        column (P2 T5). Fired ONLY from the append seam (a replace
        preserves list length); monotone — compaction removals carry
        no seam, the drift is covered by the row's ``estimated``
        label (D13). Zero-cost when disabled / unknown instance.
        """
        rec = self._get_or_create(instance_id)
        if rec is not None:
            rec.all_state_messages_len += 1

    def set_deep_bytes(
        self, instance_id: str, deep_bytes: int, *, partial_walk: bool = False
    ) -> None:
        """Deep-scan correction (T4). Cross-thread writer (sampler
        thread) ⇒ runs under the lock. Does NOT touch the charge
        counters — ``charged_bytes`` and ``deep_bytes`` are reported
        side by side and the sampler owns the coverage math (D13)."""
        if not self._enabled or not instance_id:
            return
        with self._lock:
            rec = self._records.get(instance_id)
            if rec is None:
                while len(self._records) >= self._max_records:
                    self._evict_one_locked()
                rec = InstanceChargeRecord(instance_id)
                self._records[instance_id] = rec
            rec.deep_bytes = int(deep_bytes)
            rec.deep_partial = bool(partial_walk)
            rec.deep_scans += 1
            self._seq += 1
            rec.seq = self._seq

    # ── read path ──
    def snapshot_instance(self, instance_id: str) -> Optional[dict]:
        with self._lock:
            rec = self._records.get(instance_id)
            return rec.as_dict() if rec is not None else None

    def snapshot_all(self) -> dict[str, dict]:
        with self._lock:
            return {iid: rec.as_dict() for iid, rec in self._records.items()}

    def aggregate(self) -> dict:
        """Registry-wide totals — the T1 attribution-column input."""
        with self._lock:
            recs = list(self._records.values())
        return {
            "instances_n": len(recs),
            "message_bytes": sum(r.message_bytes for r in recs),
            "checkpoint_bytes": sum(r.checkpoint_bytes for r in recs),
            "charged_bytes": sum(r.charged_bytes for r in recs),
            "deep_bytes": sum(r.deep_bytes for r in recs),
            "deep_partial_n": sum(1 for r in recs if r.deep_partial),
        }

    def register(self, instance_id: str) -> None:
        """Pre-register an instance with zeroed counters (a row must
        exist even before the first charge)."""
        self._get_or_create(instance_id)

    def reset(self, instance_id: Optional[str] = None) -> None:
        """Drop one instance's record, or all records when ``None``."""
        with self._lock:
            if instance_id is None:
                self._records.clear()
            else:
                self._records.pop(instance_id, None)


# ── Module-level singleton + seam-facing wrappers ────────────────────

_registry = ChargeRegistry()


def get_charge_registry() -> ChargeRegistry:
    """The process-wide registry (sampler/facade lane consumes this)."""
    return _registry


def set_enabled(enabled: bool) -> None:
    """Master switch on the singleton. The monitor lane calls
    ``set_enabled(True)`` at facade start and ``set_enabled(False)``
    at stop (until wired, every seam is an identity no-op)."""
    _registry.set_enabled(enabled)


def is_enabled() -> bool:
    return _registry.enabled


def note_message_append(instance_id: str, obj: Any) -> None:
    """Seam primitive (D2): charge the deep size of an appended message."""
    if not _registry._enabled or not instance_id:
        return
    try:
        size, _nodes, _partial = bounded_deep_size(obj)
    except Exception:
        logger.warning(
            "resource-charge: append sizing failed (instance %.8s)",
            instance_id,
            exc_info=True,
        )
        return
    _registry.add_message_bytes(instance_id, size)
    # T5: cheap append count for the T2 all_state_messages_len column.
    _registry.increment_message_count(instance_id)


def note_message_replace(
    instance_id: str, new_obj: Any, old_obj: Any
) -> None:
    """Seam primitive (D2): charge the DELTA on replace — never the
    full history. Same-object re-emission ⇒ zero-cost no-op."""
    if not _registry._enabled or not instance_id or new_obj is old_obj:
        return
    try:
        new_size, _n, _p = bounded_deep_size(new_obj)
        old_size, _n, _p = bounded_deep_size(old_obj)
    except Exception:
        logger.warning(
            "resource-charge: replace sizing failed (instance %.8s)",
            instance_id,
            exc_info=True,
        )
        return
    _registry.add_message_bytes(instance_id, new_size - old_size)


def note_checkpoint_read(instance_id: str, payload: Any) -> None:
    """Seam primitive (D3): charge the decoded checkpoint-read payload
    size. The caller (``_SaverRetryProxy`` read methods) invokes this
    ONCE per successful outer call, after the retry wrapper has
    settled — a transient connection retry never double-charges."""
    if not _registry._enabled or not instance_id or payload is None:
        return
    try:
        size, _nodes, _partial = bounded_deep_size(payload)
    except Exception:
        logger.warning(
            "resource-charge: checkpoint sizing failed (instance %.8s)",
            instance_id,
            exc_info=True,
        )
        return
    _registry.add_checkpoint_bytes(instance_id, size)


def register(instance_id: str) -> None:
    _registry.register(instance_id)


def set_deep_bytes(
    instance_id: str, deep_bytes: int, *, partial_walk: bool = False
) -> None:
    _registry.set_deep_bytes(
        instance_id, deep_bytes, partial_walk=partial_walk
    )


def snapshot_instance(instance_id: str) -> Optional[dict]:
    return _registry.snapshot_instance(instance_id)


def snapshot_all() -> dict[str, dict]:
    return _registry.snapshot_all()


def aggregate() -> dict:
    return _registry.aggregate()


def reset(instance_id: Optional[str] = None) -> None:
    _registry.reset(instance_id)


__all__ = [
    "DEFAULT_CHARGE_DEPTH_CAP",
    "DEFAULT_CHARGE_NODE_CAP",
    "DEFAULT_DEEP_SCAN_DEPTH_CAP",
    "DEFAULT_DEEP_SCAN_NODE_CAP",
    "DEFAULT_MAX_RECORDS",
    "ChargeRegistry",
    "InstanceChargeRecord",
    "aggregate",
    "bounded_deep_size",
    "get_charge_registry",
    "is_enabled",
    "note_checkpoint_read",
    "note_message_append",
    "note_message_replace",
    "register",
    "reset",
    "set_deep_bytes",
    "set_enabled",
    "snapshot_all",
    "snapshot_instance",
]
