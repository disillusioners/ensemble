"""Event registry v1 — structured emission (T5.2 + T5.8).

D5.7 discipline: the bridge is **non-blocking and never raises into the
emitter** — a bounded in-proc ring, sync ``record()`` that returns
immediately; a full ring drops and counts ``record_dropped_total``.
Precedent: the metrics layer's accumulation discipline
(``daemon/services/metrics_query/wiring/accumulate.py``) and the sampler's
"a raising adapter never kills the tick" (``daemon/services/metrics_query/sampler.py``).

F-1 RATIONALE (the single biggest constraint on this phase — phase5-plan.md §6):
the metrics family census is CLOSED (28 verdicts / 18 registered,
``tests/unit/metrics/test_metrics_family_adjudication.py``). This module
registers ZERO families in the metrics registry — its observability is an
engine-local snapshot (D5.5), exactly like the engine's (D2.14). Publishing
``ensemble_event_registry_*`` requires the P6 T6.3 ceremony (a verdict row,
a ``REGISTERED_FAMILIES`` member, a band, and an allowlist record), never a
side effect.

D5.11: the engine consumes the registry through the ``ingest_event`` callback
registered here via :func:`set_event_sink` — the registry invokes it AFTER
``record()`` lands the entry; **no engine import from the registry** keeps the
dependency one-way, so P5 compiles and tests without the engine. Without a
sink, ``record()`` still works.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

from .catalog import (
    DEFERRED_V2_KINDS,
    KIND_INDEX,
    FailurePosture,
    KindSpec,
)

# The bounded in-proc ring (D5.7). Small on purpose: this is a debug/
# observability buffer, not a queue; the durable substrates (event rows,
# state tables, ledger) are the system's truth.
RING_CAP = 1024

# Sink contract (D5.11): cb(kind_id, payload_dict) invoked synchronously
# after the ring append. P4's engine installs ``ingest_event``.
EventSink = Callable[[str, dict[str, Any]], Any]

_lock = threading.Lock()
_ring: deque[dict[str, Any]] = deque(maxlen=RING_CAP)

# Aggregates folded by flush() (and read-through by snapshot()).
_totals: dict[str, dict[str, Any]] = {}

# Global counters (the drop-counted posture's evidence).
_counters: dict[str, int] = {
    "record_dropped_total": 0,
    "record_refused_unknown_kind_total": 0,
    "record_refused_deferred_kind_total": 0,
    "record_lag_refused_total": 0,
    "record_internal_error_total": 0,
    "record_sink_error_total": 0,
    "flush_count_total": 0,
}

_event_sink: EventSink | None = None


def _now_iso(epoch: float) -> str:
    return (
        datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    )


def _kind_spec(kind_id: str) -> KindSpec | None:
    return KIND_INDEX.get(kind_id)


def _fold_entry(entry: dict[str, Any]) -> None:
    """Fold one ring entry into the per-kind totals (mutates ``_totals``)."""
    kind_id = entry["kind_id"]
    agg = _totals.setdefault(
        kind_id,
        {"fired_total": 0, "last_fired_at": None, "last_ts": None,
         "lag_latest": None},
    )
    agg["fired_total"] += 1
    agg["last_ts"] = entry["ts"]
    agg["last_fired_at"] = _now_iso(entry["ts"])
    lag = entry.get("dims", {}).get("lag_seconds")
    if isinstance(lag, (int, float)):
        agg["lag_latest"] = float(lag)


def record(kind_id: str, **dims: Any) -> bool:
    """Record one kind emission — sync, non-blocking, never raises.

    Returns ``True`` when the entry landed in the ring (or was folded into
    the refused counters — see below), ``False`` when the kind is not
    registered / is deferred_v2.

    Catalog-first identity (F-17): an unregistered kind is REFUSED and
    counted (``record_refused_unknown_kind_total``) — the catalog, not the
    stale SQLite CHECK, decides what may fire. A ``deferred_v2`` kind is
    likewise refused (``record_refused_deferred_kind_total``): its
    substrate does not exist, so accepting a record would fabricate an
    observation of a mechanism that never fired (F-18).

    Lag honesty (D5.12 / AC7): a ``lag_seconds`` dim is only kept for
    kinds whose catalog metadata declares lag measurable (event_bus +
    state-table kinds). For an unmeasurable kind (e.g.
    ``instance_terminalized``) the dim is STRIPPED and counted
    (``record_lag_refused_total``) — the registry never relays a
    fabricated lag.

    Never raises: every failure path is counted
    (``record_internal_error_total``) — a telemetry bridge that kills the
    emitter would be strictly worse than no telemetry.
    """
    try:
        spec = _kind_spec(kind_id)
        if spec is None:
            with _lock:
                _counters["record_refused_unknown_kind_total"] += 1
            return False
        if kind_id in DEFERRED_V2_KINDS:
            with _lock:
                _counters["record_refused_deferred_kind_total"] += 1
            return False
        clean_dims = dict(dims)
        lag_meta = spec.metadata.get("lag") or {}
        if (
            "lag_seconds" in clean_dims
            and not lag_meta.get("measurable")
        ):
            clean_dims.pop("lag_seconds")
            with _lock:
                _counters["record_lag_refused_total"] += 1
        entry = {"ts": time.time(), "kind_id": kind_id, "dims": clean_dims}
        sink = None
        with _lock:
            if len(_ring) >= RING_CAP:
                # deque(maxlen) evicts the oldest on this append — count
                # the drop (the DROP_COUNTED posture's evidence).
                _counters["record_dropped_total"] += 1
            _ring.append(entry)
            sink = _event_sink
        if sink is not None:
            # After the ring append (D5.11: the sink is invoked AFTER
            # record()). A raising sink must never break the emitter —
            # the sampler discipline — and must not flip the return:
            # the observation DID land; the sink failure is counted.
            try:
                sink(kind_id, {"ts": entry["ts"], "dims": clean_dims})
            except Exception:  # noqa: BLE001
                with _lock:
                    _counters["record_sink_error_total"] += 1
        return True
    except Exception:  # noqa: BLE001 — never raise into the emitter
        try:
            with _lock:
                _counters["record_internal_error_total"] += 1
        except Exception:  # pragma: no cover — last resort
            pass
        return False


def flush() -> int:
    """Fold the ring into the per-kind totals and clear it (D5.7).

    The periodic flush is the wiring's job (P4's engine loop may call it);
    WITHOUT a flush caller ``snapshot()`` still reads the ring directly —
    observability works with zero wiring. Returns the number of entries
    folded. Never raises.
    """
    try:
        folded = 0
        with _lock:
            while _ring:
                _fold_entry(_ring.popleft())
                folded += 1
            _counters["flush_count_total"] += 1
        return folded
    except Exception:  # noqa: BLE001
        return -1


def set_event_sink(cb: EventSink | None) -> None:
    """Register (or clear with ``None``) the ``ingest_event`` callback.

    D5.11: the ENGINE installs this at start (P4's wiring — AMD-5 places
    the composition line there, NOT in P5); the registry never imports the
    engine. The callback is invoked as ``cb(kind_id, {"ts": ..., "dims":
    ...})`` synchronously after the ring append; a raising callback is
    swallowed and counted like any other record() failure.
    """
    global _event_sink
    with _lock:
        _event_sink = cb


def recent(kind_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Recent ring entries, oldest → newest (the central-logging view).

    ``kind_id=None`` returns entries across all kinds. Bounded by
    ``limit`` (newest kept). Never raises; never blocks beyond the lock.
    """
    try:
        with _lock:
            entries = [
                {
                    "ts": e["ts"],
                    "kind_id": e["kind_id"],
                    "dims": dict(e.get("dims", {})),
                }
                for e in _ring
            ]
        if kind_id is not None:
            entries = [e for e in entries if e["kind_id"] == kind_id]
        return entries[-limit:] if limit > 0 else []
    except Exception:  # noqa: BLE001
        return []


def counters() -> dict[str, int]:
    """The registry's own global counters (drop-counted posture evidence)."""
    with _lock:
        return dict(_counters)


def reset_for_tests() -> None:
    """Reset ALL registry state — TEST-ONLY (unit-pack isolation).

    Clears the ring, totals, counters and any installed sink. Never call
    from daemon code.
    """
    global _event_sink
    with _lock:
        _ring.clear()
        _totals.clear()
        for key in _counters:
            _counters[key] = 0
        _event_sink = None
