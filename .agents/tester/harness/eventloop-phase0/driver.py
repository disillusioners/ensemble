"""The N1/N3/N5 load driver for the eventloop-phase0 harness.

NOT SHIPPED — test-support only.

ENDPOINT SHAPES (verbatim FE URLs — binding ruling Q2; sources:
``frontend/src/app/services/job.service.ts`` listActiveJobs/listRecentJobs,
``mission.service.ts``, ``job-queue-indicator.component.ts`` forkJoin,
``jobs-page.store.ts`` listJobs limit=100, ``instance.service.ts``).

PROFILES
--------
``per_endpoint``  single-shot, SERIALIZED, unloaded — the floor +
                  the round-trip formula table (N4 decisive metric).
``badge``         the FE 8s forkJoin composite, sustained: FIVE legs
                  fired concurrently every 8s (jobs active, jobs
                  recent(10), missions(20), instances flat(10),
                  defer-blocked).
``home``          the FE 10s home BFS tick — instances BFS(100),
                  sustained.
``jobs_page``     the FE 30s jobs page tick — ``/api/jobs?limit=100``
                  (the no-limit ``list_work`` enrichment), sustained,
                  with the executor-saturation sampler running.

Every profile records: per-request latency (middleware), per-request
round-trips (N4), loop-lag (the daemon's own probe via /harness/lag),
and asserts its expected collected count (N5 — a silently-skipped
gate is a FAILED gate).
"""

from __future__ import annotations

import asyncio
import sys
import time
import traceback
from typing import Any

import httpx

from common import stats_ms

# ── the censused endpoint shapes (FE-verbatim) ───────────────────────────

SHAPES: dict[str, str] = {
    "instances_flat_badge": (
        "/api/instances?limit=10&offset=0&exclude_kb=true"
        "&include_descendants=false&order=activity"
    ),
    "instances_bfs_home": (
        "/api/instances?limit=100&offset=0&exclude_kb=true"
        "&include_descendants=true"
    ),
    "work": "/api/work",
    "defer_blocked": "/api/queues/defer-blocked",
    "jobs_active_badge": "/api/jobs?status=queued,active",
    "jobs_recent_badge": (
        "/api/jobs?status=completed,settled,failed,cancelled,dead_letter&limit=10"
    ),
    "jobs_page": "/api/jobs?limit=100",
    "missions_badge": "/api/missions?liveness=processing,pending,paused&limit=20",
}

#: The badge forkJoin legs (five, concurrent — the FE shape).
BADGE_LEGS = (
    "jobs_active_badge",
    "jobs_recent_badge",
    "missions_badge",
    "instances_flat_badge",
    "defer_blocked",
)


class HarnessError(RuntimeError):
    pass


async def one_request(
    client: httpx.AsyncClient, shape: str, seq: int, *, timeout: float = 120.0
) -> dict[str, Any]:
    """One tagged request; the tag key ties latency ↔ N4 round-trips."""
    tag = f"{shape}:{seq}"
    r = await client.get(
        SHAPES[shape], headers={"X-Evloop-Tag": tag}, timeout=timeout
    )
    return {"tag": tag, "shape": shape, "status": r.status_code, "seq": seq}


# ── N3: the loop-lag sampler (the daemon's OWN probe) ────────────────────

async def sample_lag(client: httpx.AsyncClient) -> dict[str, Any]:
    r = await client.get("/harness/lag", timeout=10.0)
    r.raise_for_status()
    return r.json()


async def lag_sampler(
    client: httpx.AsyncClient,
    stop: asyncio.Event,
    out: dict[str, list[float]],
    interval: float = 0.5,
) -> None:
    """Poll the daemon probe; keep the per-poll poll-latency + histogram."""
    while not stop.is_set():
        t0 = time.perf_counter()
        try:
            payload = await sample_lag(client)
            out["poll_latencies"].append(time.perf_counter() - t0)
            out["histograms"].append(payload)
            unresp = payload.get("loop_unresponsive_s")
            if unresp is not None:
                out["unresponsive_series"].append(float(unresp))
            # The probe's NEWEST sample (one per watchdog tick) — the
            # window-local lag series, built harness-side from the
            # daemon's own probe (the probe's own histogram is bounded
            # at 300 samples and saturates during long phases).
            values = payload.get("values") or []
            if values:
                out["latest_series"].append(float(values[-1]))
        except Exception as exc:  # noqa: BLE001 — sampler failure is data
            out["errors"].append(f"{type(exc).__name__}: {exc}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


def window_lag(
    samples: list[dict[str, Any]],
    start_count: int,
    unresponsive: list[float] | None = None,
    latest: list[float] | None = None,
) -> dict[str, Any]:
    """The profile-window loop-lag summary (N3).

    METHODOLOGY (full-profile correction): the probe's rolling
    histogram is BOUNDED at 300 samples — at full scale the long
    phases saturate it, so a start-index slice of ``values`` yields an
    EMPTY window (the first full run hit exactly this).  The
    window-local metric therefore comes from the per-poll
    ``loop_unresponsive_s`` series (the daemon probe's own
    instantaneous outstanding-unresponsive value, polled every 0.5 s);
    the histogram tail is kept as best-effort context with a
    ``saturated`` marker.
    """
    last = samples[-1] if samples else {}
    values = last.get("values") or []
    n_start = start_count if isinstance(start_count, int) else 0
    saturated = len(values) >= 300 and n_start >= len(values)
    tail = values[n_start:] if (not saturated and len(values) >= n_start) else values
    out: dict[str, Any] = {
        "probe_state": last.get("probe_state"),
        "never_fired": last.get("never_fired"),
        "histogram_tail_samples": len(tail),
        "histogram_tail_max_s": round(max(tail), 4) if tail else None,
        "histogram_saturated": saturated,
    }
    series = list(latest or []) or list(unresponsive or [])
    out["metric_source"] = (
        "probe latest-sample series (harness-built from the daemon probe)"
        if latest else "probe loop_unresponsive_s series"
    )
    if series:
        ordered = sorted(series)
        import math
        def pct(p: float) -> float:
            rank = max(1, min(len(ordered), math.ceil(p / 100.0 * len(ordered))))
            return ordered[rank - 1]
        out.update({
            "samples": len(series),
            "p50_s": round(pct(50), 4),
            "p95_s": round(pct(95), 4),
            "max_s": round(max(series), 4),
            # REPAIR 2026-10-30: the RAW series, so the architect's §6.4
            # G4 fence (STALL count above TICK + 0.25 s, paired BASE↔HEAD)
            # is computable.  A p95/max summary alone cannot express a
            # STALL COUNT, and the count is the fence's discriminating
            # signal (p95 is invisible to a regression affecting <5% of
            # ticks — architect §6.4).
            "series": [round(float(v), 4) for v in series],
        })
    else:
        out["samples"] = 0
        out["series"] = []
    return out


# ── executor saturation sampler (deliverable 5) ─────────────────────────

FRAME_FAMILIES = (
    "work_resolver.py",
    "repositories",
    "instances.py",
    "session.py",
)


def classify_threads() -> dict[str, Any]:
    """Snapshot every thread's stack, classify top frames.

    Uses ``sys._current_frames`` (in-process, cheap); the verbatim
    faulthandler artifact is dumped separately once per profile.
    """
    frames = sys._current_frames()
    per_family: dict[str, int] = {}
    busy_resolve = 0
    stacks: dict[str, str] = {}
    for tid, frame in frames.items():
        try:
            text = "".join(traceback.format_stack(frame))
        except Exception:  # noqa: BLE001
            continue
        stacks[hex(tid)] = text
        for fam in FRAME_FAMILIES:
            if fam in text:
                per_family[fam] = per_family.get(fam, 0) + 1
        if "work_resolver.py" in text:
            busy_resolve += 1
    return {
        "n_threads": len(frames),
        "threads_in_family": per_family,
        "busy_work_resolver_threads": busy_resolve,
        "stacks": stacks,
    }


async def executor_sampler(
    client: httpx.AsyncClient,
    stop: asyncio.Event,
    out: dict[str, Any],
    interval: float = 0.5,
    dump_every: float = 5.0,
) -> None:
    """Poll /harness/loopinfo + classify thread stacks during a profile."""
    import faulthandler

    last_dump = 0.0
    while not stop.is_set():
        t = time.monotonic()
        try:
            r = await client.get("/harness/loopinfo", timeout=10.0)
            info = r.json()
        except Exception as exc:  # noqa: BLE001
            info = {"error": f"{type(exc).__name__}: {exc}"}
        snap = classify_threads()
        snap.pop("stacks")  # series keeps the summary only
        out["series"].append(
            {"t": round(t, 3), "loopinfo": info, **snap}
        )
        if t - last_dump >= dump_every and out.get("dump_path"):
            last_dump = t
            try:
                with open(out["dump_path"], "a", encoding="utf-8") as fh:
                    fh.write(f"\n===== dump @ {t:.1f} =====\n")
                    faulthandler.dump_traceback(file=fh, all_threads=True)
                out["dumps"] += 1
            except Exception as exc:  # noqa: BLE001
                out["errors"].append(f"dump: {type(exc).__name__}: {exc}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


# ── profiles ─────────────────────────────────────────────────────────────

async def run_profile(
    child,  # DaemonChild
    name: str,
    duration_s: float,
    *,
    shapes: tuple[str, ...] | None = None,
    cadence_s: float = 8.0,
    samples_per_shape: int = 1,
    serialize: bool = False,
    with_executor_sampler: bool = False,
    lag_out: dict | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Drive one profile; returns latencies/counts/lag for the window.

    ``serialize=True`` fires shapes one-at-a-time (the unloaded floor
    + formula table); otherwise all ``shapes`` fire CONCURRENTLY every
    ``cadence_s`` (the forkJoin composite shape).
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{child.port}")
    shapes = shapes or (name,)
    result: dict[str, Any] = {"profile": name, "duration_s": duration_s}
    lag_start = 0
    lag_data: dict[str, Any] = {"poll_latencies": [], "histograms": [],
                                "unresponsive_series": [], "latest_series": [],
                                "errors": []}
    exec_data: dict[str, Any] = {"series": [], "errors": [], "dumps": 0,
                                 "dump_path": None}
    stop = asyncio.Event()
    sampler_tasks: list[asyncio.Task] = []
    tick_tasks: list[asyncio.Task] = []

    async def leg(shape: str, s: int) -> dict[str, Any]:
        try:
            return await one_request(client, shape, s)
        except Exception as exc:  # noqa: BLE001
            return {"shape": shape, "seq": s,
                    "error": f"{type(exc).__name__}: {exc}"}

    try:
        # Baseline probe count for the window slice.
        try:
            base = await sample_lag(client)
            lag_start = base.get("samples") or 0
        except Exception:  # noqa: BLE001
            lag_start = 0
        sampler_tasks.append(asyncio.create_task(
            lag_sampler(client, stop, lag_data)))
        if with_executor_sampler:
            import pathlib
            raw_dir = pathlib.Path(child.workdir) / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            exec_data["dump_path"] = str(raw_dir / f"faulthandler_{name}.txt")
            sampler_tasks.append(asyncio.create_task(
                executor_sampler(client, stop, exec_data)))

        # Reset per-request N4 bookkeeping for the window.
        child.counter.reset_requests()
        before_latencies = {k: len(v) for k, v in child.latencies.items()}

        deadline = time.monotonic() + duration_s
        seq = 0
        requests: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            tick_t0 = time.monotonic()
            if serialize:
                for shape in shapes:
                    for _ in range(samples_per_shape):
                        seq += 1
                        requests.append(await leg(shape, seq))
            else:
                tick: list[asyncio.Task] = []
                for shape in shapes:
                    seq += 1
                    tick.append(asyncio.create_task(leg(shape, seq)))
                tick_tasks.extend(tick)
                await asyncio.wait(tick, timeout=max(120.0, duration_s))
                for t in tick:  # asyncio.wait discards results — harvest them
                    if t.done() and not t.cancelled():
                        exc = t.exception()
                        if exc is None:
                            requests.append(t.result())
                        else:
                            requests.append({"error": f"{type(exc).__name__}: {exc}"})
                    elif not t.done():
                        requests.append({"error": "tick timeout"})
            # cadence pacing (the FE tick interval)
            elapsed = time.monotonic() - tick_t0
            if elapsed < cadence_s:
                await asyncio.sleep(cadence_s - elapsed)

        # drain anything still in flight
        outstanding = [t for t in tick_tasks if not t.done()]
        if outstanding:
            await asyncio.wait(outstanding, timeout=max(30.0, cadence_s * 2))
        for t in outstanding:
            if not t.done():
                t.cancel()

        stop.set()
        await asyncio.gather(*sampler_tasks, return_exceptions=True)

        # ── collect window results ──────────────────────────────
        after = {k: len(v) for k, v in child.latencies.items()}
        new_lat: dict[str, list[float]] = {}
        new_status: dict[str, list[int]] = {}
        for tag, samples in child.latencies.items():
            shape = tag.rsplit(":", 1)[0]
            taken = samples[before_latencies.get(tag, 0):]
            if taken:
                new_lat.setdefault(shape, []).extend(d for d, _ in taken)
                new_status.setdefault(shape, []).extend(s for _, s in taken)
        result["latencies"] = {
            shape: {
                "p50_ms": round(sorted(v)[len(v) // 2] * 1000, 2),
                "p95_ms": round(sorted(v)[int(len(v) * 0.95)] * 1000, 2)
                if len(v) > 1 else round(max(v) * 1000, 2),
                "max_ms": round(max(v) * 1000, 2),
                "samples": len(v),
                "statuses": sorted(set(new_status.get(shape, []))),
            }
            for shape, v in sorted(new_lat.items())
        }
        result["requests"] = requests
        result["n4"] = child.counter.snapshot()
        result["lag"] = window_lag(
            lag_data["histograms"], lag_start,
            lag_data["unresponsive_series"], lag_data["latest_series"],
        )
        result["lag_poll"] = {
            "samples": len(lag_data["poll_latencies"]),
            "errors": lag_data["errors"][:5],
        }
        result["lag_unresponsive_max_s"] = (
            max(lag_data["unresponsive_series"])
            if lag_data["unresponsive_series"] else None
        )
        if with_executor_sampler:
            result["executor"] = {
                "series": exec_data["series"],
                "dumps": exec_data["dumps"],
                "errors": exec_data["errors"][:5],
                "dump_path": exec_data["dump_path"],
            }
    finally:
        stop.set()
        for t in (*sampler_tasks, *tick_tasks):
            if not t.done():
                t.cancel()
        if own_client:
            await client.aclose()
    return result


async def synthetic_handler_load(
    child,  # DaemonChild
    duration_s: float,
    *,
    concurrency: int = 4,
) -> dict[str, Any]:
    """The architect's SYNTHETIC HANDLER-LOAD LEG (§6.1/§6.4).

    WHY THIS EXISTS — MEASURED, not theoretical.  The wedge-capture fires
    on a thread that SUSTAINS CPU, which at full scale is a jobs-enrichment
    worker, not the loop thread.  When it fires it samples every thread,
    and whether the loop thread happens to be inside the sync-DB instances
    frames at that INSTANT is luck:

      * the committed pre-repair full run: 4/5 captures had the family
        frames on the loop thread;
      * the repaired harness's first full run (2026-10-29T23:05Z, same
        tree, same fixture, same knobs): 0/8 — every capture found the
        loop thread idle in ``selectors.select``.

    A BASE positive control that fires 0/8 is not a control.  The
    architect prescribed the remedy: "the synthetic handler-load leg must
    supply >=1 capture at BASE and 0 instances-path frames at HEAD"
    (§6.1), and flagged its absence as an unscheduled tester-lane
    deliverable (§6.4).

    WHAT IT DOES.  Drives the two 100%-on-loop instance shapes
    (BFS(100) and flat(10)) continuously from a small worker pool for the
    capture window, so the loop spends ~100% of that window inside — or
    immediately adjacent to — the sync-DB frames instead of a few percent.
    The sampling target becomes the whole window rather than a lucky
    instant.

    MODE-NEUTRAL BY CONSTRUCTION.  The SAME load runs in both modes and
    issues the SAME statements; at HEAD they are simply off the loop, so
    the pin reads 0 while the load still proves the instrument was armed.
    The request COUNT therefore differs by mode (the loop is the
    bottleneck at BASE) — it is reported, never gated as an equality.
    """
    deadline = time.monotonic() + duration_s
    stats: dict[str, Any] = {
        "requests": 0, "errors": 0, "latencies_s": [],
        "concurrency": concurrency, "duration_s": duration_s,
        "shapes": ["instances_bfs_home", "instances_flat_badge"],
    }
    lock = asyncio.Lock()

    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{child.port}") as client:
        async def worker(slot: int) -> None:
            i = slot
            while time.monotonic() < deadline:
                shape = stats["shapes"][i % len(stats["shapes"])]
                i += 1
                t0 = time.perf_counter()
                try:
                    r = await client.get(SHAPES[shape], timeout=180.0)
                    async with lock:
                        stats["latencies_s"].append(round(time.perf_counter() - t0, 4))
                        if r.status_code == 200:
                            stats["requests"] += 1
                        else:
                            stats["errors"] += 1
                except Exception:  # noqa: BLE001
                    async with lock:
                        stats["errors"] += 1

        await asyncio.gather(*(worker(s) for s in range(concurrency)))

    lat = stats.pop("latencies_s")
    stats["latency"] = stats_ms(lat)
    return stats


async def wait_for_capture(
    capture_root, timeout_s: float, poll_s: float = 2.0
) -> list[str]:
    """Wait until ≥1 capture directory exists under the capture root."""
    import pathlib
    root = pathlib.Path(capture_root)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if root.is_dir():
            dirs = sorted(
                d.name for d in root.iterdir()
                if d.is_dir() and d.name != "boot-records"
            )
            if dirs:
                return dirs
        await asyncio.sleep(poll_s)
    return []
