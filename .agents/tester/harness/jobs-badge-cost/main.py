#!/usr/bin/env python3
"""jobs-badge-cost measurement harness (M3).

CLI: --mode base|head --out <dir>
Seeds a file-backed SQLite fixture and replays the badge composite
(5 legs x 8 rounds) + a watch-reconcile leg. Captures per-leg latency,
statement census, rows. Writes raw JSON to --out.

stdlib + sqlalchemy only. No network. No live contact.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import common

ROOT = Path(__file__).resolve().parents[4]  # repo root
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.config import Config
from daemon.manager import InstanceManager
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.task.repository import TaskRepository
from daemon.services.work_resolver import WorkResolverService
from daemon.services.mission_resolver import MissionResolver
from daemon.services.job_queue_service import JobQueueService
from daemon.services.job_lock_manager import JobLockManager

import seed


ROUNDS = 8


class SelectSpy:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.counts = {"instances": 0, "job_queue_items": 0, "task": 0, "total": 0}
        self._hook = None

    def __enter__(self):
        def _before(conn, cursor, statement, parameters, context, executemany):
            s = statement.strip().upper()
            if s.startswith("SELECT"):
                self.counts["total"] += 1
                if "FROM INSTANCES" in s or "FROM \"INSTANCES\"" in s:
                    self.counts["instances"] += 1
                if "JOB_QUEUE_ITEMS" in s:
                    self.counts["job_queue_items"] += 1
                if "FROM TASK" in s or "FROM TASK," in s or 'FROM "TASK"' in s:
                    self.counts["task"] += 1

        self._hook = _before
        event.listen(self.engine, "before_cursor_execute", self._hook)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._hook)


def run_leg(label, fn, engine, rounds=ROUNDS):
    lat = []
    stmt_totals = {"instances": 0, "job_queue_items": 0, "task": 0, "total": 0}
    rows = []
    for _ in range(rounds):
        with SelectSpy(engine) as spy:
            t0 = time.perf_counter()
            try:
                r = fn()
                # support awaitable
                if hasattr(r, "__await__"):
                    r = asyncio.get_event_loop().run_until_complete(r)
            except Exception as e:
                r = {"error": str(e)}
            dt = (time.perf_counter() - t0) * 1000.0
            lat.append(dt)
            for k in stmt_totals:
                stmt_totals[k] += spy.counts[k]
            # rows
            try:
                if isinstance(r, tuple) and r and isinstance(r[0], list):
                    rows.append(len(r[0]))
                elif isinstance(r, list):
                    rows.append(len(r))
                elif isinstance(r, dict):
                    v = r.get("items", r.get("jobs", []))
                    rows.append(len(v) if isinstance(v, list) else 0)
                elif hasattr(r, "items"):
                    rows.append(len(r.items))
                elif hasattr(r, "missions"):
                    rows.append(len(r.missions))
                elif hasattr(r, "rows"):
                    rows.append(len(r.rows))
                else:
                    rows.append(0)
            except Exception:
                rows.append(0)
    # average statements per round
    per_round_stmt = {k: stmt_totals[k] / rounds for k in stmt_totals}
    return {
        "latency_ms": common.agg(lat),
        "statements_per_round": per_round_stmt,
        "statements_total": stmt_totals,
        "rows": common.agg(rows),
        "rounds": rounds,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["base", "head"])
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="badge_cost_"))
    db_path = str(workdir / "badge_cost.db")

    engine = seed.make_engine(db_path)
    stats = seed.seed(engine)

    # build services
    instance_repo = SQLModelInstanceRepository(engine)

    # Lightweight stub instance manager (real InstanceManager pulls in
    # migrations that are incompatible with this host's SQLite). Provides
    # just the attributes the reconcile paths touch.
    class _StubIM:
        def __init__(self, repo):
            self._instance_repository = repo
        async def enqueue_message(self, *a, **k):
            return None
        async def get_instance(self, *a, **k):
            return None
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    manager = _StubIM(instance_repo)
    job_repo = JobRepository(engine)
    queue_repo = JobQueueRepository(engine)
    lock_repo = LockRepository(engine)
    lock_manager = JobLockManager(lock_repo)
    task_repo = TaskRepository(engine)
    work_resolver = WorkResolverService(
        task_repo=task_repo,
        job_repo=job_repo,
        instance_repo=instance_repo,
    )
    mission_resolver = MissionResolver(instance_repo=instance_repo, job_repo=job_repo)
    watcher_repo = JobWatcherRepository(engine)
    jq_service = JobQueueService(
        repository=job_repo,
        lock_manager=lock_manager,
        queue_repo=queue_repo,
        instance_manager=manager,
    )
    jq_service.set_event_loop(loop)
    jq_service.set_watcher_repo(watcher_repo)
    jq_service.set_work_resolver(work_resolver)

    # seed JobQueue rows? queue_repo needs a queue for some paths; create one
    try:
        queue_repo.create(project_id="badge-cost-project", queue_name="system_fifo_queue",
                          queue_type="fifo", concurrency_limit=1, is_system=True)
        queue_repo.create(project_id="badge-cost-project", queue_name="system_parallel_queue",
                          queue_type="parallel", concurrency_limit=3, is_system=True)
    except Exception:
        pass

    results = {
        "mode": args.mode,
        "base_sha": "213ea6c1e",
        "seed_stats": stats,
        "rounds": ROUNDS,
        "legs": {},
    }

    # Leg 1: jobs active badge (queued,active) no limit
    def leg1():
        return work_resolver.list_work(kind="job")
    results["legs"]["jobs_active_badge"] = run_leg("jobs_active_badge", leg1, engine)

    # Leg 2: completed/settled/failed/cancelled/dead_letter limit=10
    def leg2():
        # WorkResolver doesn't filter by multi-status string easily; call list_work with status
        return work_resolver.list_work(kind="job", status="completed,settled,failed,cancelled,dead_letter")
    results["legs"]["jobs_terminal_page"] = run_leg("jobs_terminal_page", leg2, engine)

    # Leg 3: missions liveness processing,pending,paused limit=20
    def leg3():
        return mission_resolver.resolve_page(limit=20, liveness=["processing", "pending", "paused"])
    results["legs"]["missions"] = run_leg("missions", leg3, engine)

    # Leg 4: instances limit=10 order=activity
    def leg4():
        return instance_repo.list(limit=10, include_descendants=False, order="activity")
    results["legs"]["instances"] = run_leg("instances", leg4, engine)

    # Leg 5: defer-blocked (measure job_repo has_active_non_deferred_work-ish). Use a lightweight query.
    def leg5():
        # call watcher_repo.get_all_active_watches to exercise unbounded watch read
        return watcher_repo.get_all_active_watches()
    results["legs"]["defer_blocked_watch_read"] = run_leg("defer_blocked", leg5, engine)

    # Watch-reconcile leg
    # seed extra watch rows already done (60). Time reconcile methods.
    async def _reconcile():
        t0 = time.perf_counter()
        with SelectSpy(engine) as spy:
            await jq_service.reconcile_terminal_watches()
            dt1 = (time.perf_counter() - t0) * 1000.0
            s1 = dict(spy.counts)
        t0 = time.perf_counter()
        with SelectSpy(engine) as spy:
            await jq_service.reconcile_held_watches_for_instance(instance_id=None)
            dt2 = (time.perf_counter() - t0) * 1000.0
            s2 = dict(spy.counts)
        return dt1, s1, dt2, s2

    dt1, s1, dt2, s2 = loop.run_until_complete(_reconcile())
    results["watch_reconcile"] = {
        "reconcile_terminal_watches_ms": dt1,
        "reconcile_terminal_watches_statements": s1,
        "reconcile_held_watches_for_instance_ms": dt2,
        "reconcile_held_watches_for_instance_statements": s2,
    }

    # loop-lag raw: not capturable in-process without daemon probe
    results["loop_lag_raw"] = None
    results["loop_lag_note"] = "ensemble_loop_lag_seconds not reachable in this in-process harness (no daemon LoopLagProbe). Analysis reads lag-1.0s floor."

    # causal split hooks
    results["causal_split_hooks"] = {
        "enrichment_count": "jobs_active_badge statements_per_round (no-LIMIT fetch + batch lookups) — M1a bounds this",
        "router_per_row": "jobs_active_badge rows x DLQ/queue-position enrichment — M1b batches these",
        "watch_read": "defer_blocked_watch_read statements (get_all_active_watches unbounded) — M1b adds active-state WHERE + to_thread bridge",
        "pool_queueing": "NOT visible in-process: no connection pool contention / queueing. Requires a multi-process or threaded load with a real pool. Stated honestly.",
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_file = out_dir / f"BASELINE.badge_cost.{args.mode}.json"
    common.write_json(out_file, results)
    print(f"wrote {out_file}")
    print(json.dumps({k: results[k] for k in ("mode", "seed_stats")}, indent=2))

    loop.close()


if __name__ == "__main__":
    main()
