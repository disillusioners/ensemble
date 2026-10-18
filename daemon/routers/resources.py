"""Resources HTTP surface — ``GET /api/resources/*`` (resource-cost-control
P1).

Three P1 endpoints (per phase1-plan §2.1 endpoint ownership):

* ``GET /api/resources/live``     — §2.8 live payload.
* ``GET /api/resources/history``  — §2.8 history points + auto-resolution.
* ``GET /api/resources/self-cost`` — §2.8 self-cost regression surface.

P3 / P4 / P6 append their endpoints (``/traceback``, ``/anomalies``,
``/control-capabilities``, ``POST /profile``) later; the router is
the single mount for the whole family.

**DI pattern.** The router depends-injects the manager via
``request.app.state.manager`` and the monitor via
``request.app.state.resource_monitor`` (set by the lifespan — T7).
Both may be ``None`` pre-init / on a degraded boot — the endpoints
degrade to the EMPTY SHAPE, never 503 (D11).

**Sync calls.** All DB reads are synchronous; the router bridges to
async via ``asyncio.to_thread`` — the project standard.

**Error envelope.** Uniform house shape:
``HTTPException(detail={"error": <str>, ...})`` — the
``daemon/routers/snapshots.py:93-107,322-337`` precedent.

**Import chain.** Re-exported by ``daemon/routers/__init__.py`` as
``resources_router``; mounted beside ``snapshots_router`` in
``daemon/api.py``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resources", tags=["resources"])


# ── Window / resolution parsing ─────────────────────────────────────────────

# window → seconds. The four windows the FE consumes (§4).
_WINDOW_SECONDS = {
    "15m": 15 * 60,
    "1h": 60 * 60,
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
}
_WINDOWS = tuple(_WINDOW_SECONDS)
_RESOLUTIONS = ("auto", "raw", "minute", "hour")
# Raw retention window (seconds) — window > this auto-falls to minute.
# Matches the default RESOURCE_MONITOR_RETENTION_RAW_MINUTES (48h).
_DEFAULT_RAW_RETENTION_SECONDS = 2880 * 60


def _validate_window(window: str) -> int:
    """Return the window's seconds; raise 400 on an unknown value."""
    if window not in _WINDOW_SECONDS:
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"invalid window {window!r}; expected one of "
                f"{list(_WINDOWS)}"
            },
        )
    return _WINDOW_SECONDS[window]


def _validate_resolution(resolution: str) -> str:
    """Return the resolution; raise 400 on an unknown value."""
    if resolution not in _RESOLUTIONS:
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"invalid resolution {resolution!r}; expected one of "
                f"{list(_RESOLUTIONS)}"
            },
        )
    return resolution


def _resolve_resolution(window_seconds: int, requested: str) -> str:
    """Apply the auto-resolution rule from §2.8.

    ``window > raw-retention`` ⇒ auto-falls to ``minute`` (or ``hour``
    when the window exceeds the minute window). Explicit resolutions
    are honored as-is (the caller asked for them).
    """
    if requested != "auto":
        return requested
    if window_seconds <= _DEFAULT_RAW_RETENTION_SECONDS:
        return "raw"
    # Beyond raw — 24h/7d use minute; nothing in the FE's four windows
    # exceeds the minute window (14d), so we never auto-pick hour here.
    return "minute"


# ── Shape builders (always-present, degraded-safe) ──────────────────────────


def _empty_live_shape() -> dict:
    """The §2.8 /live empty shape (degraded boot / no sampler).

    Every field is present with a null/zero value so the FE can
    branch on presence without ``undefined`` guards.
    """
    return {
        "ts": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "sample_level": None,
        "daemon": {
            "pid": None,
            "cpu_pct": None,
            "rss_mb": None,
            "threads": None,
            "fds": None,
            "uptime_s": None,
        },
        "cgroup": None,
        "children": [],
        "instances": [],
        "attribution": {
            "attributed_mb": 0,
            "unattributed_mb": 0,
            "coverage_pct": 0.0,
        },
        "overruns": {"count": 0, "last_ts": None},
        "self_cost": _empty_self_cost_shape(),
    }


def _empty_history_shape(resolution_used: str = "raw") -> dict:
    """The §2.8 /history empty shape."""
    return {
        "points": [],
        "resolution_used": resolution_used,
        "rollup": resolution_used,
        "truncated": False,
    }


def _empty_self_cost_shape() -> dict:
    """The §2.8 /self-cost EXACT field set (degraded default).

    ``rss_budget_mb`` reflects ``RESOURCE_MONITOR_SELF_RSS_BUDGET_MB``
    when the monitor is present; the default 50 is used when absent
    (matches the config default, D12).
    """
    return {
        "cpu_pct": None,
        "cumulative_cpu_s": 0.0,
        "buffers_bytes": 0,
        "rss_budget_mb": 50,
        "threads": None,
        "samples": 0,
        "ticks_skipped": 0,
        "flush": {"batches": 0, "bytes": 0, "errors": 0},
        "profiling": {"active": False, "capabilities": []},
    }


# ── Helpers ─────────────────────────────────────────────────────────────────


def _get_monitor(request: Request) -> Optional[Any]:
    """Return app.state.resource_monitor or None."""
    return getattr(request.app.state, "resource_monitor", None)


def _cpu_pct_from_rows(rows: list[dict]) -> Optional[float]:
    """Compute daemon cpu_pct from the last two ring rows.

    cpu_pct = (delta_cpu_usec / (interval_s * 1e6)) * 100. Requires
    two rows (the delta is between ticks). Returns None when the
    delta is not computable.
    """
    if len(rows) < 2:
        return None
    prev, cur = rows[-2], rows[-1]
    prev_total = (prev.get("cpu_user_usec") or 0) + (prev.get("cpu_sys_usec") or 0)
    cur_total = (cur.get("cpu_user_usec") or 0) + (cur.get("cpu_sys_usec") or 0)
    interval_s = cur.get("interval_s") or 30
    delta_usec = cur_total - prev_total
    if delta_usec < 0 or interval_s <= 0:
        return None
    return round((delta_usec / (interval_s * 1_000_000.0)) * 100.0, 4)


# ── /live ───────────────────────────────────────────────────────────────────


@router.get("/live")
async def get_live(request: Request) -> dict:
    """§2.8 live payload — one request-time sample of the monitor.

    Reads the sampler's ring (the runtime source) and builds the
    §2.8 shape. Degrades to ``_empty_live_shape()`` when the monitor
    is absent / disabled (D11 — never 503).
    """
    monitor = _get_monitor(request)
    if monitor is None or not getattr(monitor, "enabled", False):
        # Degraded: monitor absent or disabled. Empty shape; ts
        # reflects the request time so the FE can still render an
        # empty live card.
        return _empty_live_shape()

    try:
        rows = monitor.ring.snapshot()
        counters = monitor.sampler_counters
        last = rows[-1] if rows else None
        cpu_pct = _cpu_pct_from_rows(rows)

        daemon_block = {
            # pid rides the ring row (the sampler stamps
            # snap.daemon.pid on every tick — review F2); None only
            # when the ring is empty (no clean tick yet).
            "pid": (last.get("pid") if last else None),
            "cpu_pct": cpu_pct,
            "rss_mb": (
                round((last.get("rss_kb") or 0) / 1024.0, 2) if last else None
            ),
            "threads": last.get("threads") if last else None,
            "fds": last.get("fds") if last else None,
            "uptime_s": round(
                max(0.0, monitor.sampler._wall_clock()
                    - counters.started_at_monotonic),
                2,
            ),
        }
        cgroup_block = None
        if last is not None and last.get("cg_current_mb") is not None:
            cgroup_block = {
                "memory": {
                    "current_mb": last.get("cg_current_mb"),
                    "peak_mb": last.get("cg_peak_mb"),
                    "anon_mb": last.get("cg_anon_mb"),
                    "file_mb": last.get("cg_file_mb"),
                    "slab_mb": last.get("cg_slab_mb"),
                },
                "cpu": {
                    "usage_usec": last.get("cg_usage_usec"),
                    "throttled_usec": last.get("cg_throttled_usec"),
                    "nr_throttled": last.get("cg_nr_throttled"),
                },
            }

        overruns: dict = {"count": counters.ticks_skipped, "last_ts": None}
        if counters.last_overrun_monotonic is not None:
            # Render the monotonic timestamp as an ISO wall-clock
            # delta from now (Naive-UTC, D9).
            delta_s = max(
                0.0,
                monitor.sampler._wall_clock() - counters.last_overrun_monotonic,
            )
            overruns["last_ts"] = (
                datetime.now(timezone.utc).replace(tzinfo=None)
                - _timedelta_seconds(delta_s)
            ).isoformat()

        # ── P2 T11 — honesty surfacing (D7/D13) ──────────────────────
        # attribution / instances / children come from the sampler's
        # LAST TICK (RAM views built on the sampling thread) — the
        # request path never samples (self-cost discipline). All
        # degrade to empty when the sampler predates P2 (attribute
        # absent) — D11.
        sampler = getattr(monitor, "sampler", None)
        attrib = getattr(sampler, "_attrib", None) or {}
        attribution_block = {
            "attributed_mb": round(
                (attrib.get("attributed_bytes") or 0) / 1048576.0, 2
            ),
            "unattributed_mb": round(
                (attrib.get("unattributed_kb") or 0) / 1024.0, 2
            ),
            "coverage_pct": round(float(attrib.get("coverage_pct") or 0.0), 4),
        }
        instances_block = list(
            getattr(sampler, "last_instance_view", None) or []
        )[:50]
        children_block = list(
            getattr(sampler, "last_children_view", None) or []
        )[:50]

        return {
            "ts": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "sample_level": counters.ladder_level,
            "daemon": daemon_block,
            "cgroup": cgroup_block,
            "children": children_block,
            "instances": instances_block,
            "attribution": attribution_block,
            "overruns": overruns,
            "self_cost": _self_cost_from_monitor(monitor),
        }
    except Exception as exc:  # fail-soft: log + return empty shape
        logger.warning("[ResourcesRouter] live degraded: %s", exc)
        return _empty_live_shape()


def _timedelta_seconds(seconds: float):
    from datetime import timedelta

    return timedelta(seconds=seconds)


def _self_cost_from_monitor(monitor: Any) -> dict:
    """Build the §2.8 /self-cost payload from the monitor."""
    counters = monitor.sampler_counters
    flusher_counters = monitor.flusher_counters
    rows = monitor.ring.snapshot()
    cpu_pct = _cpu_pct_from_rows(rows)
    # buffers_bytes — approximate via ring row count * avg row size;
    # P1 exposes the in-flight flusher buffer + ring byte estimate.
    buffers_bytes = len(rows) * 250  # ~250 B/row per §2.5 budget
    threads = None
    if rows:
        threads = rows[-1].get("threads")
    return {
        "cpu_pct": cpu_pct,
        "cumulative_cpu_s": round(counters.cumulative_cpu_s, 4),
        "buffers_bytes": buffers_bytes,
        "rss_budget_mb": monitor.sampler.self_rss_budget_mb,
        "threads": threads,
        "samples": counters.ticks,
        "ticks_skipped": counters.ticks_skipped,
        "flush": {
            "batches": flusher_counters.batches,
            "bytes": flusher_counters.bytes_written,
            "errors": flusher_counters.errors,
        },
        "profiling": {
            "active": False,
            "capabilities": [],  # P6 populates
        },
    }


# ── /history ────────────────────────────────────────────────────────────────


@router.get("/history")
async def get_history(
    request: Request,
    window: str = Query(default="15m"),
    resolution: str = Query(default="auto"),
) -> dict:
    """§2.8 history points.

    Auto-resolution: ``window > raw-retention`` ⇒ ``minute`` (see
    ``_resolve_resolution``). The response carries ``resolution_used``
    + ``rollup`` (same value) + ``truncated``.

    Degrades to ``_empty_history_shape()`` when the monitor is
    absent / disabled (D11).
    """
    window_seconds = _validate_window(window)
    requested = _validate_resolution(resolution)
    resolution_used = _resolve_resolution(window_seconds, requested)

    monitor = _get_monitor(request)
    if monitor is None or not getattr(monitor, "enabled", False):
        return _empty_history_shape(resolution_used)

    try:
        manager = getattr(request.app.state, "manager", None)
        if manager is None:
            return _empty_history_shape(resolution_used)
        points = await asyncio.to_thread(
            _read_history_points,
            manager.engine,
            resolution_used,
            window_seconds,
        )
        return {
            "points": points,
            "resolution_used": resolution_used,
            "rollup": resolution_used,
            "truncated": False,
        }
    except Exception as exc:  # fail-soft
        logger.warning("[ResourcesRouter] history degraded: %s", exc)
        return _empty_history_shape(resolution_used)


def _interval_for_resolution(resolution: str) -> int:
    return {"raw": 30, "minute": 60, "hour": 3600}.get(resolution, 30)


def _floor_naive_iso(iso: str, bucket_s: int) -> str:
    """Floor a naive-UTC ISO ts to a bucket boundary (portable).

    Mirrors the rollup module's parsing contract (``...T...`` or
    ``... ...`` form, fractional seconds tolerated).
    """
    from datetime import datetime as _dt, timezone as _tz

    s = str(iso).replace("T", " ")
    if "." in s:
        s = s.split(".")[0]
    dt = _dt.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_tz.utc)
    epoch = int(dt.timestamp())
    epoch -= epoch % bucket_s
    return (
        _dt.fromtimestamp(epoch, tz=_tz.utc)
        .replace(tzinfo=None)
        .strftime("%Y-%m-%d %H:%M:%S")
    )


def _read_instances_active(
    engine: Any,
    resolution: str,
    window_seconds: int,
) -> dict[str, int]:
    """Distinct instances sampled per bucket (P2 — ``instances_active``).

    T2 (``resource_instance_sample``) is a RAW-tier-only table (no
    roll-down), so for minute/hour resolutions the raw rows are
    floored to the bucket boundary in Python (portable — no
    ``date_trunc``/``strftime`` dialect split). The ``__overflow__``
    sentinel is NOT an instance and is excluded. Bounded at 20 000
    rows (a full default 48 h raw window at the ≤64-row cap is
    ≈369 k rows — beyond the bound the counts under-count; the DB
    remains the honest durable record). Missing table ⇒ ``{}``
    (fresh boot / P1-era data).
    """
    from datetime import timedelta

    bucket_s = _interval_for_resolution(resolution)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = (cutoff - timedelta(seconds=window_seconds)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT bucket_ts, instance_id FROM "
                    "resource_instance_sample WHERE bucket_ts >= :cutoff "
                    "LIMIT 20000"
                ),
                {"cutoff": cutoff},
            ).fetchall()
    except Exception as exc:
        err = str(exc).lower()
        if "no such table" in err or "does not exist" in err:
            return {}
        raise
    buckets: dict[str, set[str]] = {}
    for ts, iid in rows:
        if not ts or not iid or iid == "__overflow__":
            continue
        buckets.setdefault(_floor_naive_iso(ts, bucket_s), set()).add(iid)
    return {k: len(v) for k, v in buckets.items()}


def _read_history_points(
    engine: Any,
    resolution: str,
    window_seconds: int,
) -> list[dict]:
    """Read history points from ``resource_daemon_bucket``.

    Sync (bridged via ``asyncio.to_thread``). Filters by the tier's
    ``interval_s`` + the window cutoff. Returns the §2.8 points
    shape. Missing table ⇒ empty list (fresh boot). P2:
    ``instances_active`` is REAL — distinct instances sampled per
    bucket from ``resource_instance_sample`` (was a P1 ``None``
    stub).
    """
    interval_s = _interval_for_resolution(resolution)
    instances_active = _read_instances_active(
        engine, resolution, window_seconds
    )
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None)
    from datetime import timedelta

    cutoff = (cutoff - timedelta(seconds=window_seconds)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    sql = (
        "SELECT bucket_ts, cpu_user_usec, cpu_sys_usec, rss_kb, "
        "cg_current_kb, threads "
        "FROM resource_daemon_bucket "
        "WHERE interval_s = :interval_s AND bucket_ts >= :cutoff "
        "ORDER BY bucket_ts ASC"
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(sql),
                {"interval_s": interval_s, "cutoff": cutoff},
            ).fetchall()
    except Exception as exc:
        err = str(exc).lower()
        if "no such table" in err or "does not exist" in err:
            return []
        raise
    out: list[dict] = []
    prev_cpu = None
    for r in rows:
        cpu_total = (r[1] or 0) + (r[2] or 0)
        point: dict = {
            "t": r[0],
            "daemon_cpu": (
                round(
                    ((cpu_total - prev_cpu) / (interval_s * 1_000_000.0))
                    * 100.0,
                    4,
                )
                if prev_cpu is not None and cpu_total >= prev_cpu
                else None
            ),
            "daemon_rss_mb": (
                round((r[3] or 0) / 1024.0, 2) if r[3] is not None else None
            ),
            "cgroup_mem_mb": (
                round((r[4] or 0) / 1024.0, 2) if r[4] is not None else None
            ),
            "threads": r[5],
            "instances_active": instances_active.get(
                _floor_naive_iso(r[0], interval_s), 0
            ),
        }
        out.append(point)
        prev_cpu = cpu_total
    return out


# ── /self-cost ──────────────────────────────────────────────────────────────


@router.get("/self-cost")
async def get_self_cost(request: Request) -> dict:
    """§2.8 self-cost — the regression surface.

    EXACT field set:
    ``{cpu_pct, cumulative_cpu_s, buffers_bytes, rss_budget_mb,
       threads, samples, ticks_skipped, flush:{batches,bytes,errors},
       profiling:{active,capabilities}}``.

    Degrades to ``_empty_self_cost_shape()`` (200, never 503 — D11).
    """
    monitor = _get_monitor(request)
    if monitor is None or not getattr(monitor, "enabled", False):
        return _empty_self_cost_shape()
    try:
        return _self_cost_from_monitor(monitor)
    except Exception as exc:  # fail-soft
        logger.warning("[ResourcesRouter] self-cost degraded: %s", exc)
        return _empty_self_cost_shape()
