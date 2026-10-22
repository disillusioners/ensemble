"""The capture's artifact collectors (D19 / D20 / D21 / A5 / A6 / T7).

EVERY COLLECTOR DEGRADES ONE ARTIFACT, NEVER THE CAPTURE
--------------------------------------------------------
A collector that raises writes its ``status: "failed:<ExcName>"``
marker into its own artifact and returns; the capture continues.

THE POOL COUNTERS USE THE **A2** VOCABULARY
-------------------------------------------
``psycopg_pool`` 3.3.1's ``get_stats()`` returns
``pool_min`` / ``pool_max`` / ``pool_size`` / ``pool_available`` plus
the request counters (``requests_num`` / ``requests_errors`` /
``requests_queued`` / ``requests_waiting`` / ``requests_wait_ms``).
The daemon's saver pool is the ASYNC pool, so ``requests_waiting``
exists there and NOT on the sync pool.

The canonical names are therefore:

* ``pool_checked_out`` — **DERIVED** ``pool_size − pool_available``,
  clamped at 0 (there is no native key);
* ``pool_size_current`` ← ``pool_size``;
* ``pool_max`` ← ``pool_max``;
* ``pool_waiting_clients`` ← ``requests_waiting`` (async pool only);
* ``pool_checkout_timeouts`` — **ALWAYS** ``None``, with
  ``pool_checkout_timeouts_reason = "not_exposed_by_psycopg_pool_3.3.1"``
  (``requests_errors`` is a SUPERSET — it counts every waiting
  exception plus ``TooManyRequests`` — and must never be presented as
  a timeout count);
* ``pool_requests_errors`` ← ``requests_errors``;
* ``pool_counters_source`` — ``"psycopg_pool.get_stats"`` or
  ``"unavailable:<reason>"``.

``pool_size_total`` is **RETIRED** — it is ambiguous (current or
cumulative?) and a fabricated number in an evidence artifact is worse
than a missing one. There is **NO** ``pg_stat_activity`` fallback:
the daemon's own connections carry an EMPTY ``application_name``, so
such a count would be coarse AND fabricated.

READINESS IS REPORTED FROM THE COMPOSITE, NEVER RE-PROBED (D19)
-----------------------------------------------------------------
``app.state.readiness_composite`` is published by REPLACEMENT
(Starlette's ``State`` is lock-free) ⇒ safe to read cross-thread.
The composite is ``None`` until the first refresher tick — and then
the ``readyz.json`` artifact is **OMITTED** (never ``null``, never a
crash — D25).

``checked_at`` is **tz-aware** while the boot epoch is **naive-UTC**:
the two are never subtracted from one another. The composite's age is
computed against an aware ``now``.
"""

from __future__ import annotations

import faulthandler
import gc
import os
import resource
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from daemon.services.resource_monitor.proc_readers import (
    _parse_status_kb,
    _read_cgroup_memory,
    _self_cgroup_path,
)

#: The default readiness refresher interval, in seconds. The composite
#: is stale when its age exceeds 2× this.
DEFAULT_REFRESH_INTERVAL_S = 10.0

#: The pool field that is never exposed by ``psycopg_pool`` 3.3.1.
POOL_CHECKOUT_TIMEOUTS_REASON = "not_exposed_by_psycopg_pool_3.3.1"

#: The A2 canonical pool counter names.
POOL_CHECKED_OUT = "pool_checked_out"
POOL_SIZE_CURRENT = "pool_size_current"
POOL_MAX = "pool_max"
POOL_WAITING_CLIENTS = "pool_waiting_clients"
POOL_CHECKOUT_TIMEOUTS = "pool_checkout_timeouts"
POOL_CHECKOUT_TIMEOUTS_REASON_KEY = "pool_checkout_timeouts_reason"
POOL_REQUESTS_ERRORS = "pool_requests_errors"
POOL_COUNTERS_SOURCE = "pool_counters_source"

#: The default ``/proc`` root.
PROC_ROOT = Path("/proc")


def _read_text(path: Path) -> str | None:
    """Read a text file, or ``None``."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def resolve_hostname() -> str:
    """The host name — resolved ONCE at watchdog start (A5 / RK-27).

    The NSS/DNS lookup happens HERE, never inside the capture
    deadline: a name-resolution stall inside the capture would hang
    the capture.
    """
    try:
        return socket.gethostname()
    except Exception:  # pragma: no cover — defensive
        return "unknown"


def collect_stacks(path: Path) -> dict[str, Any]:
    """Dump every thread's Python stack into ``path`` (D8 / T7).

    ``faulthandler.dump_traceback`` needs a **fileno-bearing** file:
    an ``io.StringIO`` raises ``UnsupportedOperation``. The handle is
    a REAL file, and every exception (including ENOSPC) is swallowed
    into a ``status`` marker.
    """
    status: dict[str, Any] = {"status": "ok"}
    handle = None
    try:
        handle = open(path, "w", encoding="utf-8", errors="replace")
        faulthandler.dump_traceback(file=handle, all_threads=True)
        handle.flush()
    except Exception as exc:  # noqa: BLE001 — one artifact degrades
        status = {"status": f"failed:{type(exc).__name__}"}
    finally:
        try:
            if handle is not None:
                handle.close()
        except Exception:  # pragma: no cover — defensive
            pass
    return status


def collect_memory(*, object_census: bool = False) -> dict[str, Any]:
    """GC stats + RSS + the cgroup memory line (D20).

    ``ru_maxrss`` is a **PEAK**, so it is labelled ``rss_peak_kb`` —
    never ``rss_kb``. The O(heap) object census is OFF by default and
    runs AT MOST ONCE per capture.
    """
    payload: dict[str, Any] = {
        "gc": [],
        "rss_kb": None,
        "rss_peak_kb": None,
        "cgroup_memory": None,
        "object_census": None,
    }
    try:
        payload["gc"] = [dict(entry) for entry in gc.get_stats()]
    except Exception as exc:  # noqa: BLE001
        payload["gc_error"] = type(exc).__name__

    status_text = _read_text(PROC_ROOT / "self" / "status")
    if status_text:
        try:
            parsed = _parse_status_kb(status_text)
            payload["rss_kb"] = parsed.get("VmRSS")
        except Exception:  # noqa: BLE001
            pass

    try:
        payload["rss_peak_kb"] = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:  # noqa: BLE001
        payload["rss_peak_kb"] = None

    try:
        cgroup_path = _self_cgroup_path(PROC_ROOT, Path("/sys/fs/cgroup"))
        if cgroup_path is not None:
            memory = _read_cgroup_memory(cgroup_path)
            if memory is not None:
                payload["cgroup_memory"] = {
                    "current_mb": getattr(memory, "current_mb", None),
                    "peak_mb": getattr(memory, "peak_mb", None),
                    "anon_mb": getattr(memory, "anon_mb", None),
                    "file_mb": getattr(memory, "file_mb", None),
                    "slab_mb": getattr(memory, "slab_mb", None),
                }
    except Exception:  # noqa: BLE001
        payload["cgroup_memory"] = None

    if object_census:
        try:
            counts: dict[str, int] = {}
            for obj in gc.get_objects():
                kind = type(obj).__name__
                counts[kind] = counts.get(kind, 0) + 1
            top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:25]
            payload["object_census"] = {
                "total_objects": sum(counts.values()),
                "top_types": [{"type": name, "count": count} for name, count in top],
            }
        except Exception as exc:  # noqa: BLE001
            payload["object_census"] = {"status": f"failed:{type(exc).__name__}"}

    return payload


def _pool_stats(pool: Any) -> dict[str, Any] | None:
    """Read ``get_stats()`` defensively; ``None`` when it is unavailable."""
    if pool is None:
        return None
    getter = getattr(pool, "get_stats", None)
    if getter is None:
        return None
    try:
        stats = getter()
    except Exception:  # noqa: BLE001
        return None
    return stats if isinstance(stats, dict) else None


def collect_pool(pool: Any) -> dict[str, Any]:
    """The saver-pool counters, in the A2 vocabulary (D21 / A2).

    Args:
        pool: The resolved pool object (the saver's ``AsyncConnectionPool``),
            or ``None`` when the backend is not PostgreSQL.

    Returns:
        The canonical counter block. An ABSENT field is ``None`` **plus
        a reason** — never ``0``.
    """
    if pool is None:
        return {
            POOL_COUNTERS_SOURCE: "unavailable:not_postgres_backend",
            POOL_CHECKED_OUT: None,
            POOL_SIZE_CURRENT: None,
            POOL_MAX: None,
            POOL_WAITING_CLIENTS: None,
            POOL_CHECKOUT_TIMEOUTS: None,
            POOL_CHECKOUT_TIMEOUTS_REASON_KEY: POOL_CHECKOUT_TIMEOUTS_REASON,
            POOL_REQUESTS_ERRORS: None,
            "pool_field_reasons": {
                POOL_CHECKED_OUT: "not_postgres_backend",
                POOL_SIZE_CURRENT: "not_postgres_backend",
                POOL_MAX: "not_postgres_backend",
                POOL_WAITING_CLIENTS: "not_postgres_backend",
                POOL_REQUESTS_ERRORS: "not_postgres_backend",
            },
        }

    stats = _pool_stats(pool)
    if stats is None:
        return {
            POOL_COUNTERS_SOURCE: "unavailable:no_pool_attribute",
            POOL_CHECKED_OUT: None,
            POOL_SIZE_CURRENT: None,
            POOL_MAX: None,
            POOL_WAITING_CLIENTS: None,
            POOL_CHECKOUT_TIMEOUTS: None,
            POOL_CHECKOUT_TIMEOUTS_REASON_KEY: POOL_CHECKOUT_TIMEOUTS_REASON,
            POOL_REQUESTS_ERRORS: None,
            "pool_field_reasons": {
                POOL_CHECKED_OUT: "no_pool_attribute",
                POOL_SIZE_CURRENT: "no_pool_attribute",
                POOL_MAX: "no_pool_attribute",
                POOL_WAITING_CLIENTS: "no_pool_attribute",
                POOL_REQUESTS_ERRORS: "no_pool_attribute",
            },
        }

    reasons: dict[str, str] = {}

    size = stats.get("pool_size")
    available = stats.get("pool_available")
    if isinstance(size, int) and isinstance(available, int):
        checked_out = max(0, size - available)
    else:
        checked_out = None
        if "pool_size" not in stats or "pool_available" not in stats:
            reasons[POOL_CHECKED_OUT] = "field_absent"
            reasons[POOL_SIZE_CURRENT] = "field_absent"

    waiting = stats.get("requests_waiting")
    if not isinstance(waiting, int):
        waiting = None
        reasons[POOL_WAITING_CLIENTS] = "field_absent_in_this_pool_type"

    errors = stats.get("requests_errors")
    if not isinstance(errors, int):
        errors = None
        reasons[POOL_REQUESTS_ERRORS] = "field_absent_in_this_pool_type"

    max_size = stats.get("pool_max")
    if not isinstance(max_size, int):
        max_size = None
        reasons[POOL_MAX] = "field_absent"

    return {
        POOL_COUNTERS_SOURCE: "psycopg_pool.get_stats",
        POOL_CHECKED_OUT: checked_out,
        POOL_SIZE_CURRENT: size if isinstance(size, int) else None,
        POOL_MAX: max_size,
        POOL_WAITING_CLIENTS: waiting,
        # ALWAYS None — never a predicate, never read as ">= 1".
        POOL_CHECKOUT_TIMEOUTS: None,
        POOL_CHECKOUT_TIMEOUTS_REASON_KEY: POOL_CHECKOUT_TIMEOUTS_REASON,
        POOL_REQUESTS_ERRORS: errors,
        "pool_field_reasons": reasons,
    }


def collect_readiness(
    state: Any,
    *,
    refresh_interval_s: float = DEFAULT_REFRESH_INTERVAL_S,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """The readyz snapshot at capture time (D19 / A6 / D25).

    Returns:
        The snapshot, or ``None`` when the composite is not yet
        published — the caller then **OMITS** the ``readyz.json``
        artifact entirely (never ``null``, never a crash).

    The ``queue_max_age_seconds`` ``None`` is reported WITH its
    reason: ``probe_timeout`` (the probe did not answer) vs
    ``empty`` (no RUNNING tasks — which is also how a pre-epoch-only
    RUNNING set reads, because the readiness code collapses them).
    """
    composite = None
    if state is not None:
        try:
            composite = getattr(state, "readiness_composite", None)
        except Exception:  # noqa: BLE001
            composite = None
    if composite is None:
        return None

    moment = now or datetime.now(timezone.utc)
    checked_at = getattr(composite, "checked_at", None)
    composite_age_s: float | None = None
    stale = False
    if isinstance(checked_at, datetime):
        # ``checked_at`` is tz-AWARE; ``moment`` is tz-aware. The boot
        # epoch (naive-UTC) is NEVER subtracted from it.
        reference = checked_at if checked_at.tzinfo else checked_at.replace(tzinfo=timezone.utc)
        composite_age_s = max(0.0, (moment - reference).total_seconds())
        stale = composite_age_s > (2.0 * max(refresh_interval_s, 0.1))

    reasons = []
    try:
        reasons = list(getattr(composite, "reasons", []) or [])
    except Exception:  # noqa: BLE001
        reasons = []

    queue_max_age = getattr(composite, "queue_max_age_seconds", None)
    queue_reason: str | None = None
    if queue_max_age is None:
        if any("timed out" in str(reason) for reason in reasons):
            queue_reason = "probe_timeout"
        elif getattr(composite, "inflight_turns", None) is None:
            queue_reason = "probe_timeout"
        else:
            queue_reason = "empty"

    return {
        "ready": bool(getattr(composite, "ready", False)),
        "reasons": reasons,
        "database": getattr(composite, "database", None),
        "queue_freshness": getattr(composite, "queue_freshness", None),
        "services": getattr(composite, "services", None),
        "checkpoint_saver": getattr(composite, "checkpoint_saver", None),
        "forced_degraded": bool(getattr(composite, "forced_degraded", False)),
        "inflight_turns": getattr(composite, "inflight_turns", None),
        "queue_max_age_seconds": queue_max_age,
        "queue_max_age_seconds_reason": queue_reason,
        "checked_at": checked_at.isoformat() if isinstance(checked_at, datetime) else None,
        "composite_age_s": composite_age_s,
        "stale": stale,
        "refresh_interval_s": refresh_interval_s,
    }


def collect_process_info() -> dict[str, Any]:
    """The process identity — pid, threads, the observer's own tid."""
    try:
        thread_count = threading.active_count()
    except Exception:  # pragma: no cover — defensive
        thread_count = None
    try:
        native_id = threading.get_native_id()
    except Exception:  # pragma: no cover — defensive
        native_id = None
    return {
        "pid": os.getpid(),
        "thread_count": thread_count,
        "observer_native_id": native_id,
        "captured_at_monotonic": time.monotonic(),
    }


__all__ = [
    "DEFAULT_REFRESH_INTERVAL_S",
    "POOL_CHECKED_OUT",
    "POOL_CHECKOUT_TIMEOUTS",
    "POOL_CHECKOUT_TIMEOUTS_REASON",
    "POOL_CHECKOUT_TIMEOUTS_REASON_KEY",
    "POOL_COUNTERS_SOURCE",
    "POOL_MAX",
    "POOL_REQUESTS_ERRORS",
    "POOL_SIZE_CURRENT",
    "POOL_WAITING_CLIENTS",
    "collect_memory",
    "collect_pool",
    "collect_process_info",
    "collect_readiness",
    "collect_stacks",
    "resolve_hostname",
]
