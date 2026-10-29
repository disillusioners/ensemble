"""Resources HTTP surface — ``GET /api/resources/*`` (resource-cost-control
P1; P3 appends ``/traceback``).

Three P1 endpoints (per phase1-plan §2.1 endpoint ownership):

* ``GET /api/resources/live``     — §2.8 live payload.
* ``GET /api/resources/history``  — §2.8 history points + auto-resolution.
* ``GET /api/resources/self-cost`` — §2.8 self-cost regression surface.

P3 appends the drill-down endpoint:

* ``GET /api/resources/traceback`` — §2.8 joined window view (the
  join itself lives in ``daemon/services/resource_traceback.py``;
  this module stays the thin HTTP seam + the LIVE structural
  providers, which must not be imported by the join layer — the
  excluded-spine pin scopes to that module).

P4 appends the anomaly endpoints:

* ``GET /api/resources/anomalies`` — the finding journal as lean
  ``Anomaly`` rows (window-filtered, deterministic ``(t_start,
  series_key)`` order so the FE's markers never reshuffle between
  8 s polls).
* ``GET /api/resources/anomalies/{id}/evidence`` — the §2.8
  drill-down payload; samples re-derive from the retained bucket
  window, pids/children re-derive from T2/T3 via the P3 joins.
* the ``/traceback`` handler now injects the journal's in-window
  lean rows as the ``findings[]`` fill (the P3-reserved key) via
  ``build_window_view(findings_provider=...)`` — absent engine ⇒
  ``[]`` (P3 shape verbatim).

P6 appends the OBSERVE-ONLY control seam:

* ``GET /api/resources/control-capabilities`` — §2.8 static shape
  ``{enforcement:"observe-only", actions:[{name,user_gated,
  available:false},…], policy_rows}`` — it ADVERTISES the seam,
  never capability (every action ``available:false``; the T4 policy
  table is empty by design).
* ``POST /api/resources/profile`` — the profile-action SEAM ONLY
  (D2b): refuses when ``profiling_enabled=false`` (403), returns
  409 while a profile reservation is open, clamps ``seconds ≤
  profile_timebox_s``, capability-probes, and counts invocations.
  It NEVER invokes a profiler, NEVER resolves a backend, NEVER
  writes a row — the profiler itself is a separate commission
  behind this seam. The T3 dormant columns stay NULL and the T4
  policy table stays EMPTY (both pinned by the control-seam pack;
  this docstring deliberately does not name the dormant columns —
  the dormancy audit allows only the model definition to).

The router is the single mount for the whole family.

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
import gc
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

# C-1 hardening parity — the router's first-boot tolerances use the
# SAME narrowed RELATION-missing matcher as the flusher (the bare
# "does not exist" substring also matches PG column errors 42703).
from daemon.services.resource_monitor.flusher import (
    RAW_TIER_INTERVAL_PREDICATE,
)
from daemon.services.resource_monitor.flusher import (
    is_missing_relation_error as _is_missing_relation_error,
)
from daemon.services.resource_monitor.sampler import (
    DEGRADED_SENTINEL_INTERVAL_S,
)
from daemon.services import resource_traceback
from daemon.services.timestamps import to_utc_iso

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


def _sample_rows(monitor: Any) -> list[dict]:
    """The ring rows that carry a REAL sample.

    The raw ring also carries the DEGRADED SENTINEL rows — the tick
    whose platform read produced no snapshot (the DB evidence path for
    a dropped sample, written by the sampler so the flusher carries it
    into T1). A sentinel carries NO measurement: every daemon metric
    is NULL and its ``interval_s`` is the degraded marker (0).

    The live views must show the last REAL sample, never a sentinel's
    NULLs — rendering them would show "0.0 MB RSS" for a daemon whose
    memory was never measured. The sentinel belongs to the DB
    evidence path, not to the live view.
    """
    rows = monitor.ring.snapshot()
    return [
        r for r in rows
        if r.get("interval_s") != DEGRADED_SENTINEL_INTERVAL_S
    ]


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

    NOT A LIVENESS PROBE (R4, disclosed — the behaviour is NOT
    changed): the ``daemon`` block is built from the sampler's RING,
    and the flusher DRAINS that ring on every flush window — so in the
    window between a flush and the next sampler tick the daemon block
    is ALL-NULL (``pid`` / ``cpu_pct`` / ``rss_mb`` / ``threads`` /
    ``fds``). On a host whose platform reader never produces a sample
    (a dark host) the block stays NULL. This endpoint reports the
    RESOURCE sample, NOT the process's liveness: use the daemon's own
    ``/livez`` / ``/readyz`` for liveness.
    """
    monitor = _get_monitor(request)
    if monitor is None or not getattr(monitor, "enabled", False):
        # Degraded: monitor absent or disabled. Empty shape; ts
        # reflects the request time so the FE can still render an
        # empty live card.
        return _empty_live_shape()

    try:
        rows = _sample_rows(monitor)
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
        instances_block = [
            dict(row) for row in (
                getattr(sampler, "last_instance_view", None) or []
            )[:50]
        ]
        children_block = [
            dict(row) for row in (
                getattr(sampler, "last_children_view", None) or []
            )[:50]
        ]

        # W-5 (P2 review): the landed FE types
        # (frontend/src/app/models/resources.models.ts) declare
        # ``agent`` / ``in_flight_tasks`` / ``cpu_pct`` NON-null.
        # The sampler legitimately emits nulls (``agent`` None for
        # charge-only D11 instances, ``in_flight_tasks`` None on a
        # provider degrade, ``cpu_pct`` None on first sight before
        # a delta anchor exists) — coalesce HERE at the router so
        # the wire shape matches the FE contract. The copies above
        # keep the sampler's cached RAM views honest (None = "not
        # known"); the /live wire is the presentation seam.
        for row in instances_block:
            if row.get("agent") is None:
                row["agent"] = ""
            if row.get("in_flight_tasks") is None:
                row["in_flight_tasks"] = 0
        for row in children_block:
            if row.get("cpu_pct") is None:
                row["cpu_pct"] = 0.0

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
    rows = _sample_rows(monitor)
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


def _history_tier_predicate(resolution: str) -> tuple[str, dict]:
    """Return the ``(sql_fragment, params)`` that selects ONE history tier.

    The MINUTE and HOUR tiers are the ROLL-UP tiers: their discriminator
    is fixed (``interval_s = 60`` / ``3600``) and they are selected with
    an equality on the value from :func:`_interval_for_resolution`.

    The RAW tier is different: its discriminator is the EFFECTIVE
    sampling interval (``config.sample_seconds`` — default 30, range
    15..60, plus the self-throttle doublings), so a fixed ``interval_s
    = 30`` equality returned NOTHING at a 15 s configuration (the
    "raw" resolution came back empty while the raw tier was full). The
    raw tier is therefore selected by its RANGE ``0 < interval_s <
    60`` — the same predicate the retention sweep uses
    (``flusher.RAW_TIER_INTERVAL_PREDICATE``). The range excludes the
    minute tier (60), the hour tier (3600) and the DEGRADED SENTINEL
    (0).

    The predicate is NOT ``interval_s IN (30, 60, 3600)`` — that would
    mix the three tiers into one series.
    """
    if resolution == "raw":
        return (
            RAW_TIER_INTERVAL_PREDICATE,
            {},
        )
    interval_s = _interval_for_resolution(resolution)
    return ("interval_s = :interval_s", {"interval_s": interval_s})


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

    W-4: the LIMIT is paired with ``ORDER BY bucket_ts DESC`` —
    without an ORDER BY the row order is unspecified (SQLite
    rowid / PG heap order), so past the cap the DROPPED rows were
    the NEWEST buckets and ``instances_active`` read 0 at the
    newest end. DESC keeps the newest buckets inside the bound
    (the documented under-count then applies only to the OLDEST
    end, which is also what the FE timeline renders first-cut).
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
                    "ORDER BY bucket_ts DESC LIMIT 20000"
                ),
                {"cutoff": cutoff},
            ).fetchall()
    except Exception as exc:
        if _is_missing_relation_error(exc):
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
    tier_sql, tier_params = _history_tier_predicate(resolution)
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
        "cg_current_kb, threads, interval_s "
        "FROM resource_daemon_bucket "
        f"WHERE {tier_sql} AND bucket_ts >= :cutoff "
        "ORDER BY bucket_ts ASC"
    )
    params = {"cutoff": cutoff}
    params.update(tier_params)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(sql),
                params,
            ).fetchall()
    except Exception as exc:
        if _is_missing_relation_error(exc):
            return []
        raise
    out: list[dict] = []
    prev_cpu = None
    for r in rows:
        cpu_total = (r[1] or 0) + (r[2] or 0)
        # The CPU% divisor is the ROW's own interval: the raw tier
        # carries the effective sampling interval, which is not
        # necessarily the resolution's nominal 30 s (a 15 s
        # configuration would otherwise report double the real CPU%).
        row_interval_s = r[6] if r[6] else interval_s
        point: dict = {
            "t": r[0],
            "daemon_cpu": (
                round(
                    ((cpu_total - prev_cpu) / (row_interval_s * 1_000_000.0))
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


# ── /traceback (P3) — response models ───────────────────────────────────────
#
# D3 home: THIS module. P1 established no separate schema module for
# /api/resources (the P1 endpoints return plain dicts; the general
# ``daemon/routers/schemas.py`` home carries job-queue/checkpoint
# schemas only), so the traceback models live beside the endpoint —
# keeping the byte-for-byte FE contract adjacent to the seam it
# guards. Field names/order mirror
# ``frontend/src/app/models/resources.models.ts`` (:202-248 at HEAD)
# EXACTLY: ``TracebackPayload`` + its children. All list/scalar
# optionals present-as-null on the wire; ``findings`` is key-reserved
# for P4 and always ``[]`` in P3.


class TracebackWindow(BaseModel):
    """FE ``TracebackWindow`` — the normalized aware-UTC window echo."""

    t0: str
    t1: str


class TracebackInstanceAlive(BaseModel):
    """FE ``TracebackInstanceAlive`` — alive-at-window-end instance."""

    instance_id: str
    agent: Optional[str] = None
    status: Optional[str] = None


class TracebackInFlightTask(BaseModel):
    """FE ``TracebackInFlightTask`` — in-flight-at-window-end task."""

    task_id: Optional[str] = None
    work_id: Optional[str] = None
    instance_id: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None


class TracebackSpawnedChild(BaseModel):
    """FE ``TracebackSpawnedChild`` — child process alive in-window."""

    pid: Optional[int] = None
    kind: str
    name: Optional[str] = None
    spawned_at: Optional[str] = None
    exited_at: Optional[str] = None
    instance_id: Optional[str] = None


class TracebackStructural(BaseModel):
    """FE ``TracebackStructural`` — LIVE counters (null mid-boot)."""

    queue_depth: Optional[int] = None
    workers: Optional[int] = None
    checkpoints: Optional[int] = None
    gc_events: Optional[int] = None


class TracebackResponse(BaseModel):
    """FE ``TracebackPayload`` — the §2.8 traceback wire shape.

    Declared as the endpoint's ``response_model`` so the wire is
    contract-enforced by construction: the join layer's richer
    internal rows (J3 agent hop, J4 bridge, J5 resource fields,
    ``_degraded``) are stripped here — the layer API is P4's
    consumption path for those.
    """

    window: TracebackWindow
    instances_alive: list[TracebackInstanceAlive] = []
    in_flight_tasks: list[TracebackInFlightTask] = []
    spawned_children: list[TracebackSpawnedChild] = []
    structural: TracebackStructural
    findings: list[Any] = []


# ── P4 — /anomalies + evidence response models ──────────────────────────────
#
# Element shapes BYTE-FOR-BYTE per the FE-frozen contract
# (frontend/src/app/models/resources.models.ts): ``Anomaly`` /
# ``AnomaliesPayload`` / ``EvidenceContributor`` / ``EvidencePayload``
# (+ the e2e stub shapes the FE gate plays against). The journal's
# FULL explainability finding (baseline as {method,value,n} object,
# deviation, contributors) is deliberately FLATTENED here: the FE
# ``Anomaly.baseline`` is a NUMBER — the full §2.7 payload lives in
# the journal (contract-tested there), the wire stays FE-frozen.


class ResourceAnomaly(BaseModel):
    """FE ``Anomaly`` — the exact key set (marker + drill-down row).

    ``id`` is process-local (changes across daemon restarts — accepted,
    the FE re-fetches on every 8 s poll). ``t_start``/``t_end`` carry
    the P1 naive-UTC bucket format so markers align with the /history
    axis via the FE's ``parseNaiveUtc``.
    """

    id: str
    kind: str
    t_start: str
    t_end: str
    peak: float
    baseline: float
    severity: str
    series_key: str
    shape: str
    instance_id: Optional[str] = None


class AnomaliesResponse(BaseModel):
    """FE ``AnomaliesPayload`` — EXACTLY ``{anomalies: [...]}``.

    No extra keys: the FE's truncated-banner rides the /history body's
    ``truncated`` field (landed in P1), deliberately NOT here.
    """

    anomalies: list[ResourceAnomaly] = []


class EvidenceTaskStatus(BaseModel):
    """FE ``EvidenceTaskStatus`` — via the J2/J3 task join."""

    status: str
    worker_id: Optional[str] = None


class EvidenceContributorEvidence(BaseModel):
    """FE ``EvidenceContributorEvidence`` — free-form sections."""

    samples: Optional[list[Any]] = None
    first_seen: Optional[str] = None
    task_status: Optional[EvidenceTaskStatus] = None


class EvidenceContributor(BaseModel):
    """FE ``EvidenceContributor`` — share ranked; the ``unattributed``
    row is the explicit honesty remainder (share_pct sums ≤100)."""

    instance_id: Optional[str] = None
    agent_id: Optional[str] = None
    work_id: Optional[str] = None
    share_pct: float
    evidence: Optional[EvidenceContributorEvidence] = None
    unattributed: Optional[bool] = None


class EvidenceResponse(BaseModel):
    """FE ``EvidencePayload`` — the §2.8 drill-down shape.

    ``samples`` re-derives from the retained bucket window;
    ``top_pids_per_bucket`` / ``children_delta`` re-derive from T2/T3
    reads through the P3 join layer at request time.
    """

    anomaly: ResourceAnomaly
    samples: list[Any] = []
    top_pids_per_bucket: list[Any] = []
    children_delta: list[Any] = []
    contributors: list[EvidenceContributor] = []


def _empty_traceback_shape(t0_raw: str, t1_raw: str) -> dict:
    """The §2.8 /traceback empty shape (degrade — never 503, D11).

    Echoes the window normalized when parseable, verbatim otherwise —
    the shape must stay valid even when the params themselves are
    garbage (the 422 path handles those BEFORE this builder runs).
    """
    try:
        window = {
            "t0": to_utc_iso(resource_traceback.parse_window_param("t0", t0_raw)),
            "t1": to_utc_iso(resource_traceback.parse_window_param("t1", t1_raw)),
        }
    except Exception:
        window = {"t0": t0_raw, "t1": t1_raw}
    return {
        "window": window,
        "instances_alive": [],
        "in_flight_tasks": [],
        "spawned_children": [],
        "structural": {
            "queue_depth": None,
            "workers": None,
            "checkpoints": None,
            "gc_events": None,
        },
        "findings": [],
    }


# ── /traceback (P3) — LIVE structural providers ─────────────────────────────
#
# The structural counters read LIVE daemon state at query time. They
# are built HERE and injected into the join layer as callables so
# ``resource_traceback.py`` stays free of the excluded spines (the
# boot-wiped queue + the checkpoint store are never imported there —
# the unit pack's AST pin enforces it). Every provider may raise; the
# layer guards each into ``null`` (plan task 6 / R5).


def _traceback_structural_providers(manager: Any) -> dict[str, Callable[[], Any]]:
    """Build the LIVE structural probes over the manager (best-effort)."""
    engine = getattr(manager, "engine", None)

    def _queue_depth() -> int:
        # LIVE depth only — the queue is boot-wiped; historical queue
        # state does not exist (§2.6 exclusion). Outstanding-work rows:
        # pending/ready/processing/retrying.
        if engine is None:
            raise RuntimeError("engine unavailable")
        with engine.connect() as conn:
            return int(
                conn.execute(
                    text(
                        "SELECT COUNT(*) FROM message_queue "
                        "WHERE status IN "
                        "('pending','ready','processing','retrying')"
                    )
                ).scalar()
                or 0
            )

    def _workers() -> int:
        # Started-worker count across both pools
        # (``worker_pool.get_stats()["num_workers"] == len(_workers)``).
        total = 0
        found = False
        for attr in ("_worker_pool", "_chat_worker_pool"):
            pool = getattr(manager, attr, None)
            if pool is None:
                continue
            found = True
            total += int((pool.get_stats() or {}).get("num_workers") or 0)
        if not found:
            raise RuntimeError("no worker pool on manager")
        return total

    def _checkpoints() -> int:
        # Count/estimate ONLY (plan task 6) — the store has zero time
        # columns, so no historical join is attempted anywhere.
        if engine is None:
            raise RuntimeError("engine unavailable")
        with engine.connect() as conn:
            return int(
                conn.execute(
                    text("SELECT COUNT(*) FROM checkpoints")
                ).scalar()
                or 0
            )

    def _gc_events() -> int:
        # Cumulative gc collections across generations since boot.
        return int(
            sum(int(g.get("collections") or 0) for g in gc.get_stats())
        )

    return {
        "queue_depth": _queue_depth,
        "workers": _workers,
        "checkpoints": _checkpoints,
        "gc_events": _gc_events,
    }


# ── /traceback (P3) — endpoint ──────────────────────────────────────────────


@router.get("/traceback", response_model=TracebackResponse)
async def get_traceback(
    request: Request,
    response: Response,
    t0: str = Query(..., description="Window start, ISO-8601 (Z-suffixed accepted; naive ⇒ UTC)"),
    t1: str = Query(..., description="Window end, ISO-8601 (Z-suffixed accepted; naive ⇒ UTC)"),
    kind: Optional[str] = Query(
        default=None,
        description="Filter spawned_children to one kind (e.g. 'chart_render' — the 8.3 GB-class query)",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
        description="Per-list row cap (default 100, hard cap 500)",
    ),
) -> TracebackResponse:
    """§2.8 joined window view — "why was RAM high at <T>?".

    Thin seam: params → ``asyncio.to_thread(resource_traceback.
    build_window_view, …)`` → the FE-frozen ``TracebackPayload``.
    Caller input errors (unparseable t0/t1, ``t1 < t0``, span >
    168 h) are 422; INTERNAL failures degrade to the empty-but-valid
    shape with HTTP 200 + a WARN log + an
    ``X-Resources-Traceback-Degraded`` response header carrying the
    per-section reason (never 503 — D11, snapshots DI precedent).
    The header is how "join yielded nothing" stays distinguishable
    from "query failed" on the wire without breaking the frozen body
    contract.
    """
    manager = getattr(request.app.state, "manager", None)
    engine = getattr(manager, "engine", None) if manager is not None else None
    providers = (
        _traceback_structural_providers(manager)
        if manager is not None
        else None
    )
    # P4 — the journal's in-window lean rows fill the reserved
    # ``findings[]`` key. Absent engine (degraded boot / kill-switch)
    # ⇒ provider None ⇒ [] (the P3 contract shape, verbatim).
    monitor = _get_monitor(request)
    anomaly_engine = getattr(monitor, "anomaly", None) if monitor is not None else None
    findings_provider = None
    if anomaly_engine is not None:

        def _findings_provider() -> list:
            t0a = resource_traceback.parse_window_param("t0", t0)
            t1a = resource_traceback.parse_window_param("t1", t1)
            return anomaly_engine.findings_in_window(t0a, t1a, limit=limit)

        findings_provider = _findings_provider
    try:
        view = await asyncio.to_thread(
            resource_traceback.build_window_view,
            engine,
            t0,
            t1,
            kind=kind,
            limit=limit,
            structural_providers=providers,
            findings_provider=findings_provider,
        )
    except ValueError as exc:
        # Caller input error — parameter validation, not degradation.
        raise HTTPException(status_code=422, detail={"error": str(exc)}) from exc
    except Exception as exc:  # fail-soft: log + empty shape (D11)
        logger.warning("[ResourcesRouter] traceback degraded: %s", exc)
        response.headers["X-Resources-Traceback-Degraded"] = "internal-error"
        return _empty_traceback_shape(t0, t1)

    degraded = view.pop("_degraded", None) or []
    if degraded:
        response.headers["X-Resources-Traceback-Degraded"] = (
            ";".join(str(d) for d in degraded)[:200]
        )
        logger.warning(
            "[ResourcesRouter] traceback partial degrade: %s", degraded
        )
    return view


# ── P4 — /anomalies + /anomalies/{id}/evidence ──────────────────────────────


def _get_anomaly_engine(request: Request) -> Optional[Any]:
    """The anomaly engine off the monitor facade, or ``None``.

    ``None`` on a degraded boot (no monitor), a disabled monitor, or
    the anomaly kill-switch — every consumer degrades to the EMPTY
    shape (never 503, D11).
    """
    monitor = _get_monitor(request)
    if monitor is None or not getattr(monitor, "enabled", False):
        return None
    return getattr(monitor, "anomaly", None)


@router.get("/anomalies", response_model=AnomaliesResponse)
async def get_anomalies(
    request: Request,
    window: str = Query(
        default="1h",
        description="Marker window — one of 15m|1h|24h|7d (default 1h)",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
        description="Max markers returned (default 100, hard cap 500)",
    ),
) -> AnomaliesResponse:
    """§2.8 anomaly markers — the journal as lean ``Anomaly`` rows.

    Ordering is deterministic ``(t_start, series_key)`` and ids are
    stable within the process lifetime — the FE's markers must not
    reshuffle between its 8 s polls (finding ids change across daemon
    restarts; the FE re-fetches, accepted per phase4-plan).

    Degrade contract: unknown ``window`` ⇒ 400 (house error envelope);
    engine absent/disabled or ANY internal failure ⇒ 200 with the
    empty shape — never 503 (D11, snapshots DI precedent).
    """
    window_seconds = _validate_window(window)
    engine = _get_anomaly_engine(request)
    if engine is None:
        return AnomaliesResponse(anomalies=[])
    try:
        rows = await asyncio.to_thread(
            engine.list_anomalies, window_seconds, limit
        )
        return AnomaliesResponse(
            anomalies=[ResourceAnomaly(**row) for row in rows]
        )
    except Exception as exc:  # fail-soft: empty shape (D11)
        logger.warning("[ResourcesRouter] anomalies degraded: %s", exc)
        return AnomaliesResponse(anomalies=[])


@router.get("/anomalies/{anomaly_id}/evidence", response_model=EvidenceResponse)
async def get_anomaly_evidence(
    request: Request,
    anomaly_id: str,
) -> EvidenceResponse:
    """§2.8 drill-down — ``{anomaly, samples, top_pids_per_bucket,
    children_delta, contributors}`` for one finding.

    ``samples`` re-derive from the retained bucket window; the pid /
    children sections re-derive from T2/T3 through the P3 join layer
    at request time. Unknown id ⇒ 404 (house envelope); ANY internal
    failure ⇒ 404 as well — never 503 (the caller treats a missing
    drill-down as "no evidence", the degrade mapping the phase plan
    pins).
    """
    engine = _get_anomaly_engine(request)

    def _not_found(reason: str) -> HTTPException:
        return HTTPException(status_code=404, detail={"error": reason})

    if engine is None:
        raise _not_found(f"anomaly engine unavailable; id {anomaly_id!r} unknown")
    try:
        payload = await asyncio.to_thread(engine.build_evidence, anomaly_id)
    except Exception as exc:  # fail-soft: 404 (never 503 — D11)
        logger.warning(
            "[ResourcesRouter] anomaly evidence degraded: %s", exc
        )
        raise _not_found(
            f"evidence unavailable for id {anomaly_id!r}"
        ) from exc
    if payload is None:
        raise _not_found(f"unknown anomaly id {anomaly_id!r}")
    return EvidenceResponse(
        anomaly=ResourceAnomaly(**payload["anomaly"]),
        samples=list(payload.get("samples") or []),
        top_pids_per_bucket=list(payload.get("top_pids_per_bucket") or []),
        children_delta=list(payload.get("children_delta") or []),
        contributors=[
            EvidenceContributor(**c)
            for c in (payload.get("contributors") or [])
        ],
    )


# ── P6 — observe-only control seam (/control-capabilities, /profile) ────────
#
# ZERO enforcement (phase6-plan D1/D6): this section ADVERTISES the
# control seam; it never kills, throttles, or enforces anything. The
# T4 ``resource_limit_policy`` table stays EMPTY in v1 (no seed rows,
# no CRUD endpoints) and the T3 dormant columns stay NULL — both
# pinned by ``tests/test_resource_control_seam.py`` (which also
# forbids naming the dormant columns anywhere but the model
# definition), so any future enforcement code fails the suite until
# the fence is consciously lifted.

# The T4 ``op`` vocabulary (architecture §2.4) — advertised as
# ``available:false`` by /control-capabilities, NEVER implemented here.
_CONTROL_ACTION_NAMES = ("alert", "throttle", "kill")


def _count_policy_rows(engine: Any) -> int:
    """Row-count of T4 ``resource_limit_policy`` (proves v1 emptiness).

    Missing table (fresh boot before the migration/metadata pass)
    ⇒ 0 — the same tolerate-missing-relation rule the /history
    readers use. Other failures propagate to the endpoint's
    degrade wrapper (D11 — never 503).
    """
    with engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT COUNT(*) FROM resource_limit_policy")
            ).scalar()
            or 0
        )


@router.get("/control-capabilities")
async def get_control_capabilities(request: Request) -> dict:
    """§2.8 capability advertisement — observe-only, static-shaped.

    Exactly ``{enforcement:"observe-only", actions:[{name,
    user_gated, available:false},…], policy_rows}``. Every action is
    ``available:false`` — the endpoint advertises the SEAM, not
    capability (D4). ``policy_rows`` is a live repo-read of the T4
    row-count (0 by design in v1); absent manager/engine or a
    failed read degrades to ``policy_rows: 0`` — never 503 (D11).
    """
    policy_rows = 0
    manager = getattr(request.app.state, "manager", None)
    engine = getattr(manager, "engine", None) if manager is not None else None
    if engine is not None:
        try:
            policy_rows = await asyncio.to_thread(_count_policy_rows, engine)
        except Exception as exc:  # fail-soft: degrade, never 503 (D11)
            logger.warning(
                "[ResourcesRouter] control-capabilities policy_rows "
                "degraded: %s",
                exc,
            )
            policy_rows = 0
    return {
        "enforcement": "observe-only",
        "actions": [
            {"name": name, "user_gated": True, "available": False}
            for name in _CONTROL_ACTION_NAMES
        ],
        "policy_rows": policy_rows,
    }


# ── P6 — POST /profile: the profile-action SEAM ONLY (D2b) ──────────────────
#
# The profiler itself is OUT OF SCOPE (separate commission behind this
# seam). The endpoint implements ONLY the gates: refuse when
# ``profiling_enabled=false`` (403), 409 while a reservation is open,
# clamp ``seconds ≤ profile_timebox_s``, capability-probe. It never
# invokes a profiler, never resolves a backend, never writes a row —
# the ONLY state it keeps is the in-RAM reservation + the
# ``profile_seam_invocations`` counter below.


class _ProfileSeamState:
    """In-RAM reservation bookkeeping for the profile seam.

    ``active`` means a reservation window is open (a future profiler
    commission would own the real lifecycle; the seam owns only the
    gate). Monotonic-clock based so wall-clock changes cannot extend
    a reservation. ``invocations`` counts EVERY POST /profile call
    (gate outcomes included) — it is the ONLY durable trace the seam
    keeps (D2b: "never write a row beyond a
    ``profile_seam_invocations`` counter").
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active_until_monotonic: float = 0.0
        self.active_scope: Optional[str] = None
        self.invocations: int = 0

    def active(self) -> bool:
        with self._lock:
            return time.monotonic() < self.active_until_monotonic

    def reserve(self, scope: str, seconds: float) -> None:
        with self._lock:
            self.active_scope = scope
            self.active_until_monotonic = time.monotonic() + seconds

    def bump(self) -> int:
        with self._lock:
            self.invocations += 1
            return self.invocations

    def active_until_iso(self) -> Optional[str]:
        """Naive-UTC ISO of the open reservation's end (D9 posture)."""
        with self._lock:
            now = time.monotonic()
            if now >= self.active_until_monotonic:
                return None
            remaining = self.active_until_monotonic - now
        return (
            datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(seconds=remaining)
        ).isoformat()

    def reset(self) -> None:
        """Clear the reservation (tests + operator tooling)."""
        with self._lock:
            self.active_until_monotonic = 0.0
            self.active_scope = None


_profile_seam_state = _ProfileSeamState()


def _profile_capability_probe() -> dict:
    """The seam's capability probe — ALWAYS 'no profiler provisioned'.

    This is the probe's whole truth in v1: there is no backend to
    resolve (D2b forbids it), so the probe reports the absence. A
    future profiler commission replaces the body behind the same
    envelope key.
    """
    return {
        "available": False,
        "profiler": None,
        "note": (
            "observe-only seam; the profiler backend is a separate "
            "commission behind this gate"
        ),
    }


class ProfileRequestBody(BaseModel):
    """§2.8 POST /profile body — ``{scope, kind, seconds}``."""

    scope: str = Field(default="daemon")
    kind: Optional[str] = None
    seconds: Optional[int] = Field(default=None, ge=1)
    requested_by: Optional[str] = None


@router.post("/profile")
async def post_profile(request: Request, body: ProfileRequestBody) -> dict:
    """Profile-action SEAM ONLY (§2.8 / phase6-plan D2b).

    Gate order: capability config → busy → clamp → capability probe.
    Responses are the typed gate outcomes ONLY:

    * ``403`` ``{"error":…, "code":"profiling_disabled"}`` — the
      ``profiling_enabled`` config knob is false (or the config is
      absent — fail-closed; a degraded boot cannot prove enablement).
    * ``409`` ``{"error":…, "code":"profile_active", …}`` — a
      profile reservation is already open (in-RAM flag).
    * ``400`` ``{"error":…}`` — invalid ``scope``.
    * ``200`` — all gates passed: the reservation is recorded in RAM
      for the (clamped) seconds, the capability probe block is
      attached, and NO profiler runs, NO backend is resolved, and NO
      row is written (the invocation counter is in-RAM).
    """
    count = _profile_seam_state.bump()

    cfg_block = getattr(request.app.state, "ensemble_config", None)
    monitor_cfg = getattr(cfg_block, "resource_monitor", None)
    profiling_enabled = bool(getattr(monitor_cfg, "profiling_enabled", False))
    timebox_s = int(
        getattr(monitor_cfg, "profile_timebox_s", 30) or 30
    )
    if not profiling_enabled:
        raise HTTPException(
            status_code=403,
            detail={
                "error": (
                    "profiling disabled "
                    "(RESOURCE_MONITOR_PROFILING_ENABLED=false); the "
                    "profile seam is observe-only"
                ),
                "code": "profiling_disabled",
            },
        )

    scope = (body.scope or "").strip()
    if scope != "daemon" and not scope.startswith("instance:"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": (
                    "invalid scope; expected 'daemon' or 'instance:<id>'"
                )
            },
        )

    if _profile_seam_state.active():
        raise HTTPException(
            status_code=409,
            detail={
                "error": "a profile is already active",
                "code": "profile_active",
                "active_scope": _profile_seam_state.active_scope,
                "active_until": _profile_seam_state.active_until_iso(),
            },
        )

    requested = body.seconds if body.seconds is not None else timebox_s
    clamped = requested > timebox_s
    effective = min(requested, timebox_s)

    _profile_seam_state.reserve(scope, effective)
    return {
        "accepted": True,
        "scope": scope,
        "kind": body.kind,
        "requested_by": body.requested_by or "operator",
        "seconds": effective,
        "requested_seconds": requested,
        "clamped": clamped,
        "active_until": (
            datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(seconds=effective)
        ).isoformat(),
        "capability": _profile_capability_probe(),
        "profile_seam_invocations": count,
    }
