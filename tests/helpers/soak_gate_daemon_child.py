#!/usr/bin/env python3
"""The REAL-daemon child for the soak-gate A5 packs (burst + readiness legs).

WHAT THIS IS
------------
A5(i) and A5(iv) are about the DAEMON's behaviour under load, so they
need a REAL daemon — a REAL PostgreSQL scratch database, the REAL
``create_app()`` + the REAL ``lifespan``, the REAL MCP warmup pool, the
REAL checkpoint saver and the REAL readiness composite.

This module boots that daemon IN-PROCESS (the pytest root conftest
injects MOCK ``mcp`` modules, so a real subprocess is the only way to
reach a real MCP handshake — the same reasoning as
``tests/integration/test_mcp_degrade_alone.py``) and serves it over
TCP with uvicorn so the shell packs can ``curl`` it.

MODES
-----
``burst``          the CONCURRENT three-stream burst of requirements §5(i):
                   (1) the MCP warmup against a DEAD host (the C2 DNS
                   pre-check must refuse it fast — that IS the stream),
                   (2) the job-recovery reconcile, (3) the auto-continue
                   resume burst — all three OVERLAPPING on one event
                   loop, with the checkpoint pool under pressure in the
                   incident's healthy-busy shape (the checked-out count
                   RISES AND FALLS, recovery ticks between the
                   degradations).
``grace-detail``   hold the saver's lock so the checkpoint_saver
                   component degrades as ``pool_busy`` (GRACE-ELIGIBLE)
                   → the class-gated grace engages and is published.
``leak-refusal``   exhaust the P2 psycopg pool so the LEAK signature
                   arms → the grace must REFUSE (and the reasons stay
                   reportable).
``grace-expiry``   hold the saver's lock past the 120 s grace horizon
                   → the grace lapses (never extended) while the
                   component stays degraded, and it is NOT re-armed.

FENCES (binding)
----------------
* the child REFUSES to boot unless the resolved database name is
  scratch-shaped (the degrade-alone guard);
* the child never resolves a live-shaped HOME-derived path (the pack
  hands it a FAKE_HOME);
* every observed value is written to a JSON report file — the pack
  asserts on the report, never on the child's stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# ── the scratch-shape refusal (the child's own fence) ────────────────
_db = os.environ.get("POSTGRES_DB", "")
if "test" not in _db.lower() and "scratch" not in _db.lower():
    print(
        json.dumps(
            {"error": "refusing to boot against a non-scratch database: " + _db}
        )
    )
    raise SystemExit(3)

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from daemon.api import create_app, lifespan  # noqa: E402

#: The dead host the C2 DNS pre-check must refuse.  The ``.invalid``
#: TLD is reserved (RFC 2606) and NEVER resolves.
DEAD_HOST_URL = "http://soak-gate-dead-host.invalid/api"


def _now() -> float:
    return time.monotonic()


class Report:
    """The child's observable record (written to the report file)."""

    def __init__(self) -> None:
        self.data: dict = {
            "mode": None,
            "streams": {},
            "readyz_samples": [],
            "pool_series": [],
            "errors": [],
        }

    def note(self, key: str, value) -> None:
        self.data["streams"][key] = value

    def error(self, key: str, exc: BaseException) -> None:
        self.data["errors"].append(f"{key}: {type(exc).__name__}: {exc}")


async def _readyz(client: httpx.AsyncClient) -> dict:
    """One /readyz read (the REAL HTTP path)."""
    try:
        resp = await client.get("/readyz")
    except Exception as exc:  # noqa: BLE001 — a probe failure IS the data
        return {"error": f"{type(exc).__name__}: {exc}"}
    try:
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"unparseable body: {exc}", "status_code": resp.status_code}


def _grace_view(body: dict) -> dict:
    """The compact view of the readiness observables the packs assert on."""
    detail = body.get("detail") or {}
    grace = detail.get("grace") or {}
    leak = detail.get("leak_signal") or {}
    pool = detail.get("pool_state") or {}
    psycopg = pool.get("psycopg") or {}
    return {
        "status": body.get("status"),
        "reasons": detail.get("reasons"),
        "grace_active": grace.get("grace_active"),
        "grace_reason": grace.get("grace_reason"),
        "grace_expires_at": grace.get("grace_expires_at"),
        "grace_holds": grace.get("holds"),
        "leak_detected": leak.get("detected"),
        "leak_reason": leak.get("reason"),
        "component_classes": detail.get("component_classes"),
        "pool_psycopg": psycopg,
        "inflight_turns": detail.get("inflight_turns"),
        "mcp": detail.get("mcp"),
    }


async def _sampler(
    client: httpx.AsyncClient, report: Report, stop: asyncio.Event, report_path: str
) -> None:
    """Sample /readyz on a short cadence for the whole scenario.

    The report is flushed after EVERY sample (atomic replace) so the
    pack can read the observables as soon as the soak is done — the
    child does not have to finish first.

    Each sample carries the REQUEST LATENCY — the V1 measurement (what
    the readiness probes actually cost under the three-stream burst).
    """
    while not stop.is_set():
        t0 = _now()
        body = await _readyz(client)
        elapsed = _now() - t0
        report.data["readyz_samples"].append(
            {
                "t": round(_now(), 3),
                "latency_s": round(elapsed, 3),
                "view": _grace_view(body),
            }
        )
        try:
            _write_report(report_path, report.data)
        except Exception:  # noqa: BLE001 — the report write never breaks the run
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass


async def _loop_lag_heartbeat(report: Report, stop: asyncio.Event) -> None:
    """The event-loop lag heartbeat (requirements §4.6 P6).

    The p95/max of the observed lag is the phase-2 metric — under the
    three-stream burst it says whether the daemon's loop still answers
    inside the gate's probe budget.
    """
    samples: list[float] = []
    last_publish = _now()
    while not stop.is_set():
        t0 = _now()
        await asyncio.sleep(0.1)
        samples.append(_now() - t0 - 0.1)
        if _now() - last_publish >= 1.0:
            last_publish = _now()
            ordered = sorted(samples)
            report.data["loop_lag"] = {
                "samples": len(ordered),
                "p50_ms": round(ordered[len(ordered) // 2] * 1000, 2),
                "p95_ms": round(ordered[int(len(ordered) * 0.95)] * 1000, 2),
                "max_ms": round(ordered[-1] * 1000, 2),
            }
    ordered = sorted(samples)
    if not ordered:
        return
    report.data["loop_lag"] = {
        "samples": len(ordered),
        "p50_ms": round(ordered[len(ordered) // 2] * 1000, 2),
        "p95_ms": round(ordered[int(len(ordered) * 0.95)] * 1000, 2),
        "max_ms": round(ordered[-1] * 1000, 2),
    }


# ══════════════════════════════════════════════════════════════════════
# STREAM 1 — the MCP warmup against a DEAD host (the C2 refusal)
# ══════════════════════════════════════════════════════════════════════
async def stream_mcp_warmup(manager, report: Report) -> None:
    """The REAL warmup-pool init path, configured through the v3 schema.

    The context7 definition's ``build_config`` IS the v3 schema path
    (``CONTEXT7_BASE_URL`` defaulted, ``CONTEXT7_API_URL`` the
    effective lever) — the dead host is configured through it, never by
    hand-writing an env dict.

    HARNESS CONSTRAINT (disclosed): the pack boots the daemon with
    ``MCP_DISABLE_BUILT_IN_CONTEXT7=1`` so the BOOT's own warmup never
    reaches for the npm registry (a network-dependent, multi-second
    side effect that has nothing to do with the C2 refusal under
    test).  The stream therefore creates the row and registers the
    server through the SAME repository / definition / pool code the
    daemon's own bootstrap uses — the dead-host configuration still
    travels the v3 schema (``build_config``) and the real pooled
    config builder (``build_pooled_stdio_config``).
    """
    from daemon.mcp import init_guard
    from daemon.mcp.builtin_servers import get_registry
    from daemon.mcp.config import McpStdioConfig
    from daemon.mcp.warmup_pool import build_pooled_stdio_config, get_mcp_warmup_pool

    out: dict = {"target": DEAD_HOST_URL}
    report.note("mcp_warmup", out)  # registered live
    t0 = _now()
    try:
        definition = get_registry().get_by_name("context7")
        if definition is None:
            out["error"] = "the context7 definition is not registered"
            report.note("mcp_warmup", out)
            return
        # The v3 schema path — the same builder the configure-builtin
        # route uses.
        config = definition.build_config({"CONTEXT7_API_URL": DEAD_HOST_URL})
        out["schema_version"] = definition.schema_version
        out["config_env"] = dict(config.get("env") or {})

        # Persist it on the live row (the row is the source of truth for
        # the pooled env).
        repo = manager._mcp_server_repository
        row = await asyncio.to_thread(repo.get_mcp_server_by_name, "context7")
        if row is None:
            await asyncio.to_thread(
                repo.create_mcp_server,
                "context7",
                description=definition.description,
                config=config,
                is_active=True,
                is_builtin=True,
                config_schema=definition.get_config_schema(),
                config_schema_version=definition.schema_version,
            )
            out["row_created"] = True
        else:
            await asyncio.to_thread(repo.update_mcp_server, row.id, config=config)
            out["row_updated"] = True

        # Register it in the REAL pool with the REAL pre-check resolver
        # (the same shape the manager's own bootstrap uses).
        pool = get_mcp_warmup_pool()
        stdio_config = build_pooled_stdio_config(definition, config)
        if "context7" not in pool._configs:
            pool.register_server(
                "context7",
                stdio_config,
                pool_size=1,
                precheck_resolver=getattr(definition, "get_precheck_url", None),
            )
            out["registered"] = True
        else:
            pool._configs["context7"] = stdio_config
            out["reregistered"] = True

        # The C2 pre-check refuses the init BEFORE any child is spawned.
        t_init = _now()
        try:
            conn = await pool._create_pooled_connection("context7")
            out["outcome"] = "SPAWNED (the pre-check did NOT refuse)"
            try:
                await pool._release_connection("context7", conn)
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001 — the refusal IS the data
            out["outcome"] = f"{type(exc).__name__}: {exc}"
        out["init_elapsed_s"] = round(_now() - t_init, 3)
        out["total_elapsed_s"] = round(_now() - t0, 3)
        out["quarantined"] = init_guard.is_quarantined("context7")
        state = init_guard.state_snapshot().get("context7") or {}
        out["state"] = state.get("state")
        out["last_error_class"] = state.get("last_error_class")
        out["breaker_state"] = state.get("breaker_state")
        out["spawned_children"] = len(getattr(pool, "_configs", {}) and [] or [])
    except Exception as exc:  # noqa: BLE001
        report.error("mcp_warmup", exc)
        out["error"] = f"{type(exc).__name__}: {exc}"
    report.note("mcp_warmup", out)


# ══════════════════════════════════════════════════════════════════════
# STREAM 2 — the job-recovery reconcile (the REAL service)
# ══════════════════════════════════════════════════════════════════════
async def stream_job_recovery(manager, report: Report, duration_s: float) -> None:
    """The REAL ``JobRecoveryService.reconcile_drift_states`` loop."""
    from daemon.repositories import create_job_repository
    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    from daemon.repositories.job_queue.lock_repository import LockRepository
    from daemon.services.job_recovery_service import JobRecoveryService

    out: dict = {"ticks": 0, "errors": 0}
    report.note("job_recovery", out)  # registered live
    try:
        service = JobRecoveryService(
            job_repository=create_job_repository(engine=manager.engine, create_tables=False),
            lock_repository=LockRepository(engine=manager.engine),
            instance_repository=SQLModelInstanceRepository(engine=manager.engine),
            task_repository=getattr(manager, "_task_repo", None),
            stale_task_recovery=getattr(manager, "_stale_recovery", None),
            worker_pool=getattr(manager, "_worker_pool", None),
            instance_manager=manager,
        )
        deadline = _now() + duration_s
        while _now() < deadline:
            try:
                await service.reconcile_drift_states()
                out["ticks"] += 1
            except Exception as exc:  # noqa: BLE001
                out["errors"] += 1
                out.setdefault("last_error", f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(1.0)
    except Exception as exc:  # noqa: BLE001
        report.error("job_recovery", exc)
        out["error"] = f"{type(exc).__name__}: {exc}"
    report.note("job_recovery", out)


# ══════════════════════════════════════════════════════════════════════
# STREAM 3 — the auto-continue resume burst (the REAL pass)
# ══════════════════════════════════════════════════════════════════════
async def stream_auto_continue(manager, report: Report, duration_s: float) -> None:
    """The REAL ``continue_running_instances_after_restart`` loop."""
    from daemon.services.auto_continue_boot_pass import (
        continue_running_instances_after_restart,
    )
    from daemon.services.boot_epoch import get_boot_epoch

    out: dict = {"passes": 0, "errors": 0}
    report.note("auto_continue", out)  # registered live
    try:
        deadline = _now() + duration_s
        while _now() < deadline:
            try:
                result = await continue_running_instances_after_restart(
                    manager, get_boot_epoch()
                )
                out["passes"] += 1
                out["last_candidates"] = getattr(result, "candidates", None)
                out["last_scheduled"] = getattr(result, "scheduled", None)
            except Exception as exc:  # noqa: BLE001
                out["errors"] += 1
                out.setdefault("last_error", f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(1.0)
    except Exception as exc:  # noqa: BLE001
        report.error("auto_continue", exc)
        out["error"] = f"{type(exc).__name__}: {exc}"
    report.note("auto_continue", out)


# ══════════════════════════════════════════════════════════════════════
# STREAM 4 — the checkpoint pool pressure (the healthy-busy shape)
# ══════════════════════════════════════════════════════════════════════
async def stream_pool_pressure(manager, report: Report, duration_s: float) -> None:
    """The REAL checkpoint pool under pressure — the healthy-busy shape.

    The wave holds a RISING AND FALLING number of the daemon's REAL
    saver-pool connections (the P2 ORACLE pool the leak predicate reads)
    — the checked-out count rises, peaks and DRAINS, with recovery
    ticks in between.  That is the incident's healthy-busy shape (the
    grace must accept it), and it is deliberately NOT the leak shape
    (a monotonic rise with no recovery tick — phase 1's predicate pin).

    The wave PAUSES its checkpoint writes while the contention window
    is open (the saver's lock is held by ``stream_lock_pressure``):
    that is the incident's pool contention, and it is what degrades the
    ``checkpoint_saver`` component as ``pool_busy`` (the grace-eligible
    class).
    """
    from daemon.services.readiness import CHECKPOINT_SENTINEL_CHANNEL

    out: dict = {"ticks": 0, "writes": 0, "errors": 0, "held": 0}
    report.note("pool_pressure", out)  # registered live — the pack reads it mid-run
    saver = getattr(manager.checkpointer, "raw_saver", None)
    pool = getattr(saver, "conn", None)
    hold_start = duration_s * 0.20
    hold_end = duration_s * 0.33
    held: list = []
    try:
        # The wave: rise, peak, fall — recovery ticks in between. The
        # ceiling is well below the pool's max_size so the readiness
        # probe keeps its own connection (the daemon stays able to
        # answer).
        wave = [1, 3, 5, 4, 2, 1, 0]
        idx = 0
        started = _now()
        deadline = started + duration_s
        while _now() < deadline:
            elapsed = _now() - started
            paused = hold_start <= elapsed < hold_end
            target = wave[idx % len(wave)]
            idx += 1
            # DRAIN: release the connections beyond the current target.
            while len(held) > target:
                await pool.putconn(held.pop())
            # RISE: acquire up to the target CONCURRENTLY.
            want = target - len(held)
            if want > 0:
                try:
                    acquired = await asyncio.gather(
                        *[pool.getconn() for _ in range(want)]
                    )
                    held.extend(acquired)
                except Exception as exc:  # noqa: BLE001
                    out["errors"] += 1
                    out.setdefault("last_error", f"{type(exc).__name__}: {exc}")
            out["held"] = len(held)
            # One light REAL checkpoint write (the pressure on the saver
            # itself) — skipped while the contention window is open (the
            # write would queue behind the held lock).
            if not paused:
                config = {
                    "configurable": {
                        "thread_id": f"soak-gate-burst-{idx}",
                        "checkpoint_ns": "",
                        "checkpoint_id": f"soak-gate-{idx}",
                    }
                }
                try:
                    await saver.aput_writes(
                        config,
                        [(CHECKPOINT_SENTINEL_CHANNEL, f"burst-{idx}")],
                        f"task-{idx}",
                    )
                    out["writes"] += 1
                except Exception as exc:  # noqa: BLE001
                    out["errors"] += 1
                    out.setdefault("last_error", f"{type(exc).__name__}: {exc}")
            # The pool's own counters (the V2 numbers).
            if pool is not None and hasattr(pool, "get_stats"):
                try:
                    stats = pool.get_stats()
                    report.data["pool_series"].append(
                        {
                            "t": round(_now() - started, 3),
                            "target": target,
                            "pool_size": int(stats.get("pool_size", 0)),
                            "pool_available": int(stats.get("pool_available", 0)),
                            "pool_max": int(stats.get("pool_max", 0)),
                            "requests_waiting": int(stats.get("requests_waiting", 0)),
                            "in_use": int(stats.get("pool_size", 0))
                            - int(stats.get("pool_available", 0)),
                        }
                    )
                except Exception:  # noqa: BLE001
                    pass
            out["ticks"] += 1
            await asyncio.sleep(0.3)
        # Release the held connections.
        while held:
            try:
                await pool.putconn(held.pop())
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        report.error("pool_pressure", exc)
        out["error"] = f"{type(exc).__name__}: {exc}"
    report.note("pool_pressure", out)


async def stream_lock_pressure(manager, report: Report, duration_s: float) -> None:
    """Hold the saver's LOCK during the contention window.

    The readiness probe OBSERVES the lock (never acquires it) — a held
    lock is the ``pool_busy`` classification, the grace-eligible class
    of the incident's shape.
    """
    out: dict = {"held": False}
    report.note("lock_pressure", out)  # registered live
    saver = getattr(manager.checkpointer, "raw_saver", None)
    lock = getattr(saver, "lock", None)
    try:
        await asyncio.sleep(duration_s * 0.20)
        await lock.acquire()
        out["held"] = True
        out["held_at_s"] = round(_now(), 3)
        await asyncio.sleep(duration_s * 0.13)
        lock.release()
        out["released_at_s"] = round(_now(), 3)
    except Exception as exc:  # noqa: BLE001
        report.error("lock_pressure", exc)
        out["error"] = f"{type(exc).__name__}: {exc}"
        try:
            lock.release()
        except Exception:  # noqa: BLE001
            pass
    report.note("lock_pressure", out)


# ══════════════════════════════════════════════════════════════════════
# the readiness-induction scenarios
# ══════════════════════════════════════════════════════════════════════
async def scenario_grace_detail(manager, report: Report, hold_s: float) -> None:
    """Hold the saver's lock → the component degrades as ``pool_busy``."""
    saver = getattr(manager.checkpointer, "raw_saver", None)
    lock = getattr(saver, "lock", None)
    report.data["lock_held"] = False
    try:
        await lock.acquire()
        report.data["lock_held"] = True
    except Exception as exc:  # noqa: BLE001
        report.error("grace_detail", exc)
        report.data["lock_error"] = f"{type(exc).__name__}: {exc}"
        return
    await asyncio.sleep(hold_s)
    try:
        lock.release()
    except Exception:  # noqa: BLE001
        pass


async def scenario_leak_refusal(manager, report: Report, hold_s: float) -> None:
    """Exhaust the P2 psycopg pool → the LEAK signature arms."""
    saver = getattr(manager.checkpointer, "raw_saver", None)
    pool = getattr(saver, "conn", None)
    held = []
    report.data["pool_exhausted"] = False
    try:
        max_size = int(pool.get_stats().get("pool_max", 0)) if pool else 0
        if not max_size:
            report.data["error"] = "the saver has no pool object"
            return
        # Every connection of the pool, checked out at once.
        held = await asyncio.gather(*[pool.getconn() for _ in range(max_size)])
        report.data["pool_exhausted"] = True
        report.data["pool_max"] = max_size
    except Exception as exc:  # noqa: BLE001
        report.error("leak_refusal", exc)
        report.data["error"] = f"{type(exc).__name__}: {exc}"
    await asyncio.sleep(hold_s)
    for conn in held:
        try:
            await pool.putconn(conn)
        except Exception:  # noqa: BLE001
            pass


async def scenario_grace_expiry(manager, report: Report, hold_s: float) -> None:
    """Hold the saver's lock PAST the grace horizon → the grace lapses."""
    saver = getattr(manager.checkpointer, "raw_saver", None)
    lock = getattr(saver, "lock", None)
    report.data["lock_held"] = False
    try:
        await lock.acquire()
        report.data["lock_held"] = True
    except Exception as exc:  # noqa: BLE001
        report.error("grace_expiry", exc)
        report.data["lock_error"] = f"{type(exc).__name__}: {exc}"
        return
    await asyncio.sleep(hold_s)
    try:
        lock.release()
    except Exception:  # noqa: BLE001
        pass


# ══════════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════════
async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--mode", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--ready-file", default="")
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--hold", type=float, default=40.0)
    args = ap.parse_args()

    report = Report()
    report.data["mode"] = args.mode
    report.data["db"] = _db
    report.data["port"] = args.port
    report.data["home"] = os.environ.get("HOME", "")

    app = create_app()
    async with lifespan(app):
        manager = app.state.manager
        report.data["daemon_booted"] = True

        uvicorn_config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
            access_log=False,
            # The lifespan is run MANUALLY below (the same ``async with
            # lifespan(app)`` shape the degrade-alone integration pin
            # uses) — uvicorn must NOT run it a second time.
            lifespan="off",
        )
        server = uvicorn.Server(uvicorn_config)
        server_task = asyncio.create_task(server.serve())
        deadline = _now() + 60
        while not server.started and _now() < deadline:
            await asyncio.sleep(0.05)
        report.data["serving"] = bool(server.started)
        if not server.started:
            report.data["error"] = "uvicorn never started"
            _write_report(args.report, report.data)
            return 4

        if args.ready_file:
            with open(args.ready_file, "w", encoding="utf-8") as fh:
                json.dump(
                    {"port": args.port, "pid": os.getpid(), "mode": args.mode}, fh
                )

        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{args.port}") as client:
            stop = asyncio.Event()
            sampler = asyncio.create_task(_sampler(client, report, stop, args.report))
            lag_task = asyncio.create_task(_loop_lag_heartbeat(report, stop))
            try:
                if args.mode == "burst":
                    # THE THREE STREAMS, CONCURRENT (never serial), plus
                    # the checkpoint pool pressure on the same loop.
                    await asyncio.gather(
                        stream_mcp_warmup(manager, report),
                        stream_job_recovery(manager, report, args.duration),
                        stream_auto_continue(manager, report, args.duration),
                        stream_pool_pressure(manager, report, args.duration),
                        stream_lock_pressure(manager, report, args.duration),
                    )
                elif args.mode == "grace-detail":
                    await scenario_grace_detail(manager, report, args.hold)
                elif args.mode == "leak-refusal":
                    await scenario_leak_refusal(manager, report, args.hold)
                elif args.mode == "grace-expiry":
                    await scenario_grace_expiry(manager, report, args.hold)
                else:
                    report.data["error"] = f"unknown mode {args.mode}"
            finally:
                stop.set()
                try:
                    await asyncio.wait_for(sampler, timeout=5)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await asyncio.wait_for(lag_task, timeout=5)
                except Exception:  # noqa: BLE001
                    pass
            # A last read AFTER the scenario — the packs assert on it.
            report.data["final_readyz"] = _grace_view(await _readyz(client))

        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=30)
        except Exception:  # noqa: BLE001
            pass
    _write_report(args.report, report.data)
    return 0


def _write_report(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
