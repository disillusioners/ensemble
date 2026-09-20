# Preserved from /tmp/mw-replay/replay.py (instance 6953dce2, gate 2026-09-19, branch feature/mission-watch-toolset @ 0b504b5e)
# Ad-hoc SQLite tool-layer incident-replay harness — R1-R4 all PASS

#!/usr/bin/env python3
"""Incident-replay acceptance test — watch_mission tool-layer e2e (SQLite fixture).

Closes the cde5017f incident class: a mission had TWO receipts (task-style
receipt b8b6ead7 + mirror-style receipt ac213421 — mirror receipts are also
``Task.work_id`` rows on the same instance, modelled per
``instance_messaging.py:2050-2057``). The caller watched only one receipt and
the notification stranded. Under the new design (``watch_mission(target)``,
``daemon/tools/job_queue.py``), watching via EITHER handle lands ``job_watchers``
rows on ALL receipts so any receipt's terminal event fires the watch.

Scenarios (R1–R4):

  R1 — multi-receipt fan-out (the incident shape): mission_id path AND
       job-ref path both land rows for EVERY receipt.
  R2 — notify path as far as the tool layer allows: real
       ``JobWatcherRepository`` primitives — ``get_watchers_for_job`` per
       receipt + the CAS claim (``claim_watchers_for_job_for_instances``)
       per receipt on tool-registered rows.
  R3 — register-then-notify on already-terminal mission.
  R4 — ``job_create`` → response carries ``job_id`` always; ``mission_id``
       only-if-known; ``watch_mission(returned job_id)`` lands a row on
       that pre-generated UUID.

Harness: real ``MissionResolver`` + real ``JobWatcherRepository`` + real
``TaskRepository`` + real ``JobRepository`` + real ``SQLModelInstanceRepository``
against a file-backed SQLite engine (NullPool + WAL + busy_timeout +
foreign_keys=ON); ``job_service`` is an ``AsyncMock`` double (mirrors
``tests/unit/tools/test_mission_watch_tools.py``).

Constraints honoured:
  * NO file modifications inside the repo (output only via stdout + this dir).
  * NO daemon boot (fresh-SQLite boot is broken by a PG-only migration).
  * NO pytest suite execution — standalone script.

Dual-layer timeout: ``signal.alarm(280)`` (inner guard) + the caller wraps
this script in ``timeout 300 uv run python /tmp/mw-replay/replay.py``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# ── Dual-layer watchdog (Layer 2: inner guard, ≤280s) ────────────────────
WATCHDOG_SECONDS = 280


def _on_alarm(signum, frame):
    sys.stderr.write(
        f"\n[replay] INTERNAL WATCHDOG FIRED after {WATCHDOG_SECONDS}s "
        f"(signal={signum}); exiting hard.\n"
    )
    sys.stderr.flush()
    os._exit(124)


signal.signal(signal.SIGALRM, _on_alarm)
signal.alarm(WATCHDOG_SECONDS)


# ── Repo path resolution (must mirror tests/unit/tools/test_mission_watch_tools.py) ──
REPO_ROOT = Path(
    "/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble"
)


def _add_repo_to_path() -> None:
    """Add the repo to sys.path so the daemon package imports cleanly."""
    repo = str(REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)


_add_repo_to_path()


# ── Daemon imports (mirrors the test suite) ───────────────────────────────
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402
from sqlmodel import Session as SQLModelSession  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

import daemon.repositories.instance.models  # noqa: E402,F401
import daemon.repositories.job_queue.models  # noqa: E402,F401
import daemon.repositories.task.models  # noqa: E402,F401

import daemon.constants as _ens_constants  # noqa: E402

# Initialise the system-default project id so ``normalize_project_id()``
# accepts None (the job_create tool path) without raising the
# "called before system default project was initialized" RuntimeError.
# We use a stable fixture value matching the seed ``Instance.project_id``.
_ens_constants.SYSTEM_DEFAULT_PROJECT_ID = "replay-project"

from daemon.repositories.instance.models import Instance, InstanceStatus  # noqa: E402
from daemon.repositories.instance.repository import (  # noqa: E402
    SQLModelInstanceRepository,
)
from daemon.repositories.job_queue.models import AdmissionState, JobItem  # noqa: E402
from daemon.repositories.job_queue.repository import JobRepository  # noqa: E402
from daemon.repositories.job_queue.watcher_repository import (  # noqa: E402
    JobWatcherRepository,
)
from daemon.repositories.task.models import Task, TaskStatus  # noqa: E402
from daemon.repositories.task.repository import TaskRepository  # noqa: E402
from daemon.services.mission_resolver import MissionResolver  # noqa: E402
from daemon.tools.job_queue import (  # noqa: E402
    create_job_tools,
    create_mission_watch_tools,
)

# ── Constants & globals ──────────────────────────────────────────────────
CALLER = "replay-caller-0000-0000-000000000001"

SCENARIO_RESULTS: dict[str, str] = {}
SCENARIO_DETAILS: dict[str, dict] = {}

SCRIPT_START = time.monotonic()


def elapsed() -> float:
    return time.monotonic() - SCRIPT_START


def _stamp(msg: str) -> None:
    sys.stdout.write(f"[replay +{elapsed():6.2f}s] {msg}\n")
    sys.stdout.flush()


# ── Seed helpers (verbatim crib from tests/unit/tools/test_mission_watch_tools.py) ──


def _make_engine(db_path: Path) -> Engine:
    """File-backed SQLite engine: NullPool + WAL + busy_timeout + FKs.

    Mirrors ``tests/unit/tools/test_mission_watch_tools.py::engine``.
    """
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    return eng


def _seed_caller_instance(engine: Engine) -> None:
    """The watcher's instance row must exist (FK to ``instances``)."""
    now = datetime.now(timezone.utc)
    iso_now = now.isoformat()
    with SQLModelSession(engine) as s:
        s.add(
            Instance(
                instance_id=CALLER,
                agent_id="jober",
                agent_dir="agents/jober",
                project_id="replay-project",
                status=InstanceStatus.RUNNING.value,
                last_activity_at=now,
                created_at=iso_now,
                updated_at=iso_now,
            )
        )
        s.commit()


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    agent_id: str = "developer",
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    iid = instance_id or f"inst-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    iso_now = now.isoformat()
    with SQLModelSession(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id=agent_id,
                agent_dir=f"agents/{agent_id}",
                project_id="replay-project",
                status=status,
                last_activity_at=now,
                created_at=iso_now,
                updated_at=iso_now,
            )
        )
        s.commit()
    return iid


def _seed_task(
    engine: Engine, *, work_id: str, instance_id: str,
    status: str = TaskStatus.RUNNING.value,
) -> str:
    with SQLModelSession(engine) as s:
        s.add(Task(work_id=work_id, instance_id=instance_id, status=status))
        s.commit()
    return work_id


def _seed_job_item(
    engine: Engine,
    *,
    instance_id: str,
    job_id: str | None = None,
    admission_state: str = AdmissionState.DONE.value,
    job_type: str = "task",
    terminal_reason: str | None = "completed",
) -> str:
    """Seed a JobItem row (used for W4-hazard / dead-letter scenarios)."""
    jid = job_id or str(uuid.uuid4())
    with SQLModelSession(engine) as s:
        s.add(
            JobItem(
                job_id=jid,
                agent_id="developer",
                agent_dir="agents/developer",
                message="seeded",
                source="agent:test",
                instance_id=instance_id,
                admission_state=admission_state,
                job_type=job_type,
                terminal_reason=terminal_reason,
            )
        )
        s.commit()
    return jid


def _work_record(
    work_id: str, kind: str, status: str,
    *, instance_id=None, job_type=None,
    mission_liveness=None, error=None, result_summary=None,
):
    """Mirrors the WorkRecord shape used in the test suite mocks."""
    record = MagicMock(name=f"WorkRecord[{work_id[:8]}]")
    record.work_id = work_id
    record.kind = kind
    record.status = status
    record.instance_id = instance_id
    record.job_type = job_type
    record.mission_liveness = mission_liveness
    record.error = error
    record.result_summary = result_summary
    return record


def _watched_job_ids(watcher_repo: JobWatcherRepository) -> set[str]:
    return {w.job_id for w in watcher_repo.get_watches_for_instance(CALLER)}


def _build_harness(db_path: Path):
    """Build the same harness as the unit-test suite:
    real engine + real repos + AsyncMock ``job_service``.
    Returns the components needed by every scenario.
    """
    engine = _make_engine(db_path)
    _seed_caller_instance(engine)

    instance_repo = SQLModelInstanceRepository(engine)
    job_repo = JobRepository(engine)
    resolver = MissionResolver(instance_repo=instance_repo, job_repo=job_repo)
    watcher_repo = JobWatcherRepository(engine)
    task_repo = TaskRepository(engine)

    job_service = AsyncMock(name="JobQueueService")
    job_service.get_work = AsyncMock(return_value=None)
    # Real engine notify primitive (lowest callable the fixture allows).
    job_service.notify_watchers = AsyncMock(return_value=1)

    mission_watch_tool = create_mission_watch_tools(
        job_service=job_service,
        mission_resolver=resolver,
        task_repo=task_repo,
        watcher_repo=watcher_repo,
        current_instance_id=CALLER,
    )
    assert [t.name for t in mission_watch_tool] == ["watch_mission"], (
        f"unexpected mission-watch tool names: {[t.name for t in mission_watch_tool]}"
    )
    watch_mission = mission_watch_tool[0]

    return {
        "engine": engine,
        "instance_repo": instance_repo,
        "job_repo": job_repo,
        "resolver": resolver,
        "watcher_repo": watcher_repo,
        "task_repo": task_repo,
        "job_service": job_service,
        "watch_mission": watch_mission,
    }


# ── Scenario R1: multi-receipt fan-out (incident shape) ───────────────────


def run_r1(db_path: Path) -> tuple[str, dict]:
    """R1: mission_id AND job-ref both land rows on EVERY receipt.

    Shape: one mission instance with two Task.work_ids (task receipt +
    mirror receipt). Both non-terminal at the moment of watch.

    Expected:
      (a) watch_mission(mission_id) → 2 rows, both mission_terminal.
      (b) watch_mission(receipt_a) → 2 rows (same fan-out, NOT just receipt_a).
    """
    h = _build_harness(db_path)
    engine = h["engine"]
    watcher_repo = h["watcher_repo"]
    job_service = h["job_service"]
    watch_mission = h["watch_mission"]

    # Seed: one mission with two non-terminal Task receipts.
    mid = _seed_instance(engine)
    receipt_a = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
    receipt_b = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

    details: dict = {
        "mission_id": mid,
        "receipt_a": receipt_a,
        "receipt_b": receipt_b,
    }

    try:
        # ── R1.a: mission_id path ────────────────────────────────────────
        result_a = asyncio.run(watch_mission.ainvoke({"target": mid}))
        details["r1a_result"] = result_a
        watched_after_a = _watched_job_ids(watcher_repo)
        details["r1a_watched_job_ids"] = sorted(watched_after_a)
        details["r1a_notify_await_count"] = job_service.notify_watchers.await_count

        r1a_pass = (
            "Mission watch registered" in result_a
            and "2 receipt(s)" in result_a
            and watched_after_a == {receipt_a, receipt_b}
            and all(
                r.watch_events == ["mission_terminal"]
                for r in watcher_repo.get_watches_for_instance(CALLER)
            )
            # Mission is non-terminal → no immediate-notify branch.
            and job_service.notify_watchers.await_count == 0
        )
        details["r1a_pass"] = r1a_pass

        # Reset for R1.b (clean slate, fresh rows) — fresh engine so the
        # UPSERT keeps both rows but we re-run the path on receipt_a.
        # We reuse the same engine here because the test-suite pattern
        # does not reset between scenarios within a pack.

        # ── R1.b: job-ref path — watch via receipt_a ─────────────────────
        async def _get_work_for_receipt_a(work_id):
            # Resolve receipt_a onto the mission so the resolver-front-end
            # can fan out via task_repo.get_by_instance(mission_id).
            return _work_record(
                work_id, "report", "processing",
                instance_id=mid,
            )

        job_service.get_work = AsyncMock(side_effect=_get_work_for_receipt_a)

        result_b = asyncio.run(watch_mission.ainvoke({"target": receipt_a}))
        details["r1b_result"] = result_b
        watched_after_b = _watched_job_ids(watcher_repo)
        details["r1b_watched_job_ids"] = sorted(watched_after_b)

        r1b_pass = (
            "Mission watch registered" in result_b
            # The whole point of the reshape: row lands on EVERY receipt,
            # not just the one whose handle we passed.
            and watched_after_b == {receipt_a, receipt_b}
        )
        details["r1b_pass"] = r1b_pass

        verdict = "PASS" if (r1a_pass and r1b_pass) else "FAIL"
        details["verdict"] = verdict
        return verdict, details
    except Exception as e:
        details["verdict"] = "FAIL"
        details["exception"] = repr(e)
        details["traceback"] = traceback.format_exc()
        return "FAIL", details


# ── Scenario R2: notify path as far as the tool layer allows ────────────


def run_r2(db_path: Path) -> tuple[str, dict]:
    """R2: exercise REAL engine notify primitives on tool-registered rows.

    Shape: terminal mission + 2 receipts. We register rows via the
    ``watch_mission`` tool (real add_watch) and then exercise the REAL
    repo primitives the engine uses for notification:

      (i)  ``watcher_repo.get_watchers_for_job(receipt)`` — must return
           the row the tool just minted.
      (ii) ``watcher_repo.claim_watchers_for_job_for_instances(receipt, [CALLER])``
           — atomic CAS delete (the real engine claim primitive), row
           consumed.

    Depth reached: CAS delete (the engine's claim primitive). The actual
    ``enqueue_message`` is one layer deeper and would require a wired
    ``InstanceManager`` + ``WorkResolverService`` + running event loop —
    out of scope for the SQLite fixture per the task brief.
    """
    h = _build_harness(db_path)
    engine = h["engine"]
    watcher_repo = h["watcher_repo"]
    job_service = h["job_service"]
    watch_mission = h["watch_mission"]

    mid = _seed_instance(engine, status=InstanceStatus.COMPLETED.value)
    receipt_a = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
    receipt_b = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

    details: dict = {
        "mission_id": mid,
        "receipt_a": receipt_a,
        "receipt_b": receipt_b,
    }

    try:
        # Step 1 — register rows via the real tool (real add_watch UPSERT).
        result = asyncio.run(watch_mission.ainvoke({"target": mid}))
        details["register_result"] = result

        rows_post_register = watcher_repo.get_watches_for_instance(CALLER)
        details["rows_after_register"] = sorted(
            (r.job_id for r in rows_post_register)
        )
        watched_set = {r.job_id for r in rows_post_register}

        # (i) Real ``get_watchers_for_job`` per receipt — engine's
        # notify-side lookup. Must return the tool-registered row.
        lookup_a = watcher_repo.get_watchers_for_job(receipt_a)
        lookup_b = watcher_repo.get_watchers_for_job(receipt_b)
        details["lookup_a_count"] = len(lookup_a)
        details["lookup_b_count"] = len(lookup_b)
        details["lookup_a_instances"] = sorted(
            w.instance_id for w in lookup_a
        )
        details["lookup_b_instances"] = sorted(
            w.instance_id for w in lookup_b
        )
        details["lookup_a_events"] = [
            list(w.watch_events) for w in lookup_a
        ]
        details["lookup_b_events"] = [
            list(w.watch_events) for w in lookup_b
        ]

        i_pass = (
            len(lookup_a) == 1
            and lookup_a[0].instance_id == CALLER
            and list(lookup_a[0].watch_events) == ["mission_terminal"]
            and len(lookup_b) == 1
            and lookup_b[0].instance_id == CALLER
            and list(lookup_b[0].watch_events) == ["mission_terminal"]
        )
        details["i_pass"] = i_pass

        # (ii) Real CAS claim per receipt — the engine's exactly-once
        # primitive. Row must be claimed exactly once; second claim empty.
        claim_a = watcher_repo.claim_watchers_for_job_for_instances(
            receipt_a, [CALLER]
        )
        claim_a_second = watcher_repo.claim_watchers_for_job_for_instances(
            receipt_a, [CALLER]
        )
        claim_b = watcher_repo.claim_watchers_for_job_for_instances(
            receipt_b, [CALLER]
        )

        details["claim_a_count"] = len(claim_a)
        details["claim_a_second_count"] = len(claim_a_second)
        details["claim_b_count"] = len(claim_b)
        details["post_claim_watched"] = sorted(
            w.job_id for w in watcher_repo.get_watches_for_instance(CALLER)
        )

        ii_pass = (
            len(claim_a) == 1
            and claim_a[0].job_id == receipt_a
            and claim_a[0].instance_id == CALLER
            and len(claim_a_second) == 0
            and len(claim_b) == 1
            and claim_b[0].job_id == receipt_b
            and _watched_job_ids(watcher_repo) == set()
        )
        details["ii_pass"] = ii_pass

        # Depth statement — what we exercised and what was one layer
        # deeper out of reach.
        details["notify_depth"] = (
            "CAS-claim primitive (real JobWatcherRepository."
            "claim_watchers_for_job_for_instances) — the lowest real "
            "engine component the SQLite fixture allows. The actual "
            "[JOB_EVENT] envelope build + InstanceManager.enqueue_message "
            "delivery lives in work_notifier.notify_work_watchers "
            "(daemon/services/work_notifier.py:118-470) and requires a "
            "fully-wired InstanceManager + WorkResolverService — out of "
            "scope for the standalone SQLite fixture."
        )

        verdict = "PASS" if (i_pass and ii_pass) else "FAIL"
        details["verdict"] = verdict
        return verdict, details
    except Exception as e:
        details["verdict"] = "FAIL"
        details["exception"] = repr(e)
        details["traceback"] = traceback.format_exc()
        return "FAIL", details


# ── Scenario R3: register-then-notify on already-terminal ─────────────────


def run_r3(db_path: Path) -> tuple[str, dict]:
    """R3: on a genuinely-terminal mission, ``watch_mission`` registers
    rows AND fires immediate notify per receipt (per-receipt terminal
    check via ``is_terminal(record.status)``).

    Shape: COMPLETED instance + 2 Task receipts with terminal WorkRecord
    statuses so the per-receipt terminal check passes for both.

    Expected: tool reply contains 'already terminal (completed)' AND
    'immediate notification sent on 2 receipt(s)' AND ``notify_watchers``
    AsyncMock awaited once per receipt.
    """
    h = _build_harness(db_path)
    engine = h["engine"]
    watcher_repo = h["watcher_repo"]
    job_service = h["job_service"]
    watch_mission = h["watch_mission"]

    mid = _seed_instance(engine, status=InstanceStatus.COMPLETED.value)
    receipt_a = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)
    receipt_b = _seed_task(engine, work_id=str(uuid.uuid4()), instance_id=mid)

    details: dict = {
        "mission_id": mid,
        "receipt_a": receipt_a,
        "receipt_b": receipt_b,
    }

    try:
        # Configure the work-side lookup so the per-receipt terminal
        # check sees both receipts as terminal.
        async def _get_work_terminal(work_id):
            return _work_record(
                work_id, "report", "completed",
                instance_id=mid, result_summary="done",
            )

        job_service.get_work = AsyncMock(side_effect=_get_work_terminal)

        result = asyncio.run(watch_mission.ainvoke({"target": mid}))
        details["result"] = result

        # Both receipts registered.
        details["watched_job_ids"] = sorted(_watched_job_ids(watcher_repo))

        # notify_watchers was awaited per terminal receipt.
        details["notify_await_count"] = job_service.notify_watchers.await_count
        notify_args = [
            (c.args[0], c.args[1]) for c in job_service.notify_watchers.await_args_list
        ]
        details["notify_calls"] = notify_args

        # All minted rows carry mission_terminal event.
        details["all_rows_mission_terminal"] = all(
            list(r.watch_events) == ["mission_terminal"]
            for r in watcher_repo.get_watches_for_instance(CALLER)
        )

        r3_pass = (
            "already terminal (completed)" in result
            and "immediate notification sent on 2 receipt(s)" in result
            and _watched_job_ids(watcher_repo) == {receipt_a, receipt_b}
            and job_service.notify_watchers.await_count == 2
            and sorted(notify_args) == sorted(
                [(receipt_a, "completed"), (receipt_b, "completed")]
            )
            and details["all_rows_mission_terminal"] is True
        )
        details["verdict"] = "PASS" if r3_pass else "FAIL"
        return details["verdict"], details
    except Exception as e:
        details["verdict"] = "FAIL"
        details["exception"] = repr(e)
        details["traceback"] = traceback.format_exc()
        return "FAIL", details


# ── Scenario R4: complete-flow proof (chaining no longer needed) ──────────


def run_r4(db_path: Path) -> tuple[str, dict]:
    """R4: ``job_create`` → response carries ``job_id`` always;
    ``mission_id`` only-if-known. ``watch_mission(returned job_id)``
    lands a row on that pre-generated UUID — no await_mission /
    job_continue / watch_job step required.

    Shape: ``create_job_tools`` factory wired with a mocked
    ``job_service.enqueue`` that returns a real ``JobItem`` row.
    Two sub-scenarios:

      4.a Mirror-shaped job (mission already known at response time):
           JobItem.instance_id set → response includes mission_id.
      4.b Fresh task job (instance not yet minted):
           JobItem.instance_id = None → mission_id ABSENT.
      4.c watch_mission(returned job_id) on a fresh task job:
           row lands on the pre-generated UUID even though no instance
           exists yet (mission-not-born edge — design §2b).
    """
    engine = _make_engine(db_path)
    _seed_caller_instance(engine)

    instance_repo = SQLModelInstanceRepository(engine)
    job_repo = JobRepository(engine)
    resolver = MissionResolver(instance_repo=instance_repo, job_repo=job_repo)
    watcher_repo = JobWatcherRepository(engine)
    task_repo = TaskRepository(engine)

    job_service = AsyncMock(name="JobQueueService")
    job_service.get_work = AsyncMock(return_value=None)
    job_service.notify_watchers = AsyncMock(return_value=1)

    tools = create_job_tools(
        job_service=job_service,
        queue_mgmt_service=MagicMock(name="queue_mgmt"),
        dead_letter_service=MagicMock(name="dlq"),
        current_instance_id=CALLER,
        agent_id="jober",
        watcher_repo=watcher_repo,
        manager=None,
    )
    job_create = next(t for t in tools if t.name == "job_create")

    mission_watch_tools = create_mission_watch_tools(
        job_service=job_service,
        mission_resolver=resolver,
        task_repo=task_repo,
        watcher_repo=watcher_repo,
        current_instance_id=CALLER,
    )
    watch_mission = mission_watch_tools[0]

    details: dict = {}

    try:
        def _make_job_item(job_id: str, instance_id: str | None) -> JobItem:
            """Build a session-DETACHED JobItem double that exposes
            ``to_dict()`` and ``instance_id`` without a live SQLAlchemy
            session. The tool reads ``job_item.to_dict()`` and
            ``getattr(job_item, 'instance_id', None)`` — both work on a
            spec'd MagicMock without raising the 'not bound to a Session'
            error a detached real ORM row would produce.
            """
            row = MagicMock(spec=JobItem, name=f"JobItem[{job_id[:8]}]")
            row.job_id = job_id
            row.agent_id = "developer"
            row.agent_dir = "agents/developer"
            row.agent_tag = None
            row.message = "replay seed"
            row.source = "agent:test"
            row.project_id = "replay-project"
            row.queue_id = None
            row.priority = 5
            row.admission_state = (
                AdmissionState.DONE.value if instance_id
                else AdmissionState.QUEUED.value
            )
            row.created_at = datetime.now(timezone.utc).isoformat()
            row.instance_id = instance_id
            row.job_metadata = {}
            row.deleted_at = None
            row.retry_count = 0
            row.max_retries = 3
            row.idempotency_key = None
            row.job_type = "message" if instance_id else "task"
            row.next_retry_at = None
            row.failed_at = None
            row.terminal_reason = "completed" if instance_id else None
            row.to_dict = lambda: {
                "job_id": row.job_id,
                "agent_id": row.agent_id,
                "agent_dir": row.agent_dir,
                "agent_tag": row.agent_tag,
                "message": row.message,
                "source": row.source,
                "project_id": row.project_id,
                "queue_id": row.queue_id,
                "priority": row.priority,
                "admission_state": row.admission_state,
                "created_at": row.created_at,
                "instance_id": row.instance_id,
                "metadata": dict(row.job_metadata) if row.job_metadata else {},
                "deleted_at": row.deleted_at,
                "retry_count": row.retry_count,
                "max_retries": row.max_retries,
                "idempotency_key": row.idempotency_key,
                "job_type": row.job_type,
                "next_retry_at": row.next_retry_at,
                "failed_at": row.failed_at,
                "terminal_reason": row.terminal_reason,
            }
            return row

        # ── 4.a Mirror-shaped job — mission already known ────────────────
        mirror_job_id = str(uuid.uuid4())
        mirror_mission_id = _seed_instance(engine)
        mirror_item = _make_job_item(mirror_job_id, mirror_mission_id)

        async def _enqueue_mirror(*args, **kwargs):
            return mirror_item

        job_service.enqueue = AsyncMock(side_effect=_enqueue_mirror)

        resp_a = asyncio.run(job_create.ainvoke({
            "agent_id": "developer",
            "message": "mirror-shaped work",
        }))
        details["r4a_response"] = resp_a
        details["r4a_has_job_id"] = "job_id" in resp_a
        details["r4a_has_mission_id"] = "mission_id" in resp_a
        details["r4a_mission_id_matches"] = (
            resp_a.get("mission_id") == mirror_mission_id
        )

        r4a_pass = (
            "error" not in resp_a
            and resp_a.get("job_id") == mirror_job_id
            and resp_a.get("mission_id") == mirror_mission_id
        )
        details["r4a_pass"] = r4a_pass

        # ── 4.b Fresh task job — instance not yet minted ────────────────
        fresh_job_id = str(uuid.uuid4())
        fresh_item = _make_job_item(fresh_job_id, None)

        async def _enqueue_fresh(*args, **kwargs):
            return fresh_item

        job_service.enqueue = AsyncMock(side_effect=_enqueue_fresh)

        resp_b = asyncio.run(job_create.ainvoke({
            "agent_id": "developer",
            "message": "fresh task work",
        }))
        details["r4b_response"] = resp_b
        details["r4b_has_job_id"] = "job_id" in resp_b
        details["r4b_has_mission_id"] = "mission_id" in resp_b

        r4b_pass = (
            "error" not in resp_b
            and resp_b.get("job_id") == fresh_job_id
            and "mission_id" not in resp_b
        )
        details["r4b_pass"] = r4b_pass

        # ── 4.c watch_mission on the fresh pre-generated UUID ────────────
        # Pre-dispatch: receipt UUID resolves with instance_id=None via
        # job_service.get_work.
        async def _get_work_pre_dispatch(work_id):
            return _work_record(
                work_id, "job", "pending", instance_id=None,
            )

        job_service.get_work = AsyncMock(side_effect=_get_work_pre_dispatch)

        result_c = asyncio.run(watch_mission.ainvoke({"target": fresh_job_id}))
        details["r4c_result"] = result_c
        watched_c = _watched_job_ids(watcher_repo)
        details["r4c_watched_job_ids"] = sorted(watched_c)

        r4c_pass = (
            "Watch registered on receipt" in result_c
            and "mission not yet dispatched" in result_c
            and watched_c == {fresh_job_id}
            and list(
                watcher_repo.get_watches_for_instance(CALLER)[0].watch_events
            ) == ["mission_terminal"]
        )
        details["r4c_pass"] = r4c_pass

        verdict = "PASS" if (r4a_pass and r4b_pass and r4c_pass) else "FAIL"
        details["verdict"] = verdict
        return verdict, details
    except Exception as e:
        details["verdict"] = "FAIL"
        details["exception"] = repr(e)
        details["traceback"] = traceback.format_exc()
        return "FAIL", details


# ── Reporter ──────────────────────────────────────────────────────────────


def _emit_summary() -> int:
    """Emit the final RESULT block + per-scenario verdicts. Returns exit code."""
    overall_pass = all(v == "PASS" for v in SCENARIO_RESULTS.values())
    overall_fail = any(v == "FAIL" for v in SCENARIO_RESULTS.values())
    overall = "PASS" if overall_pass else ("FAIL" if overall_fail else "PARTIAL")

    print("\n" + "=" * 72)
    print(f"OVERALL RESULT: {overall}")
    print(f"Total runtime: {elapsed():.2f}s (inner watchdog cap {WATCHDOG_SECONDS}s)")
    print("=" * 72)
    print()
    print("Per-scenario verdicts:")
    for name in ("R1", "R2", "R3", "R4"):
        verdict = SCENARIO_RESULTS.get(name, "NOT_RUN")
        print(f"  {name}: {verdict}")
        details = SCENARIO_DETAILS.get(name, {})
        # Show key evidence lines per scenario (compact).
        for k, v in details.items():
            if k in ("verdict", "exception", "traceback"):
                continue
            if isinstance(v, list) and len(v) > 8:
                v = f"<{len(v)} items: {v[:3]}...>"
            if isinstance(v, dict) and len(v) > 8:
                v = f"<{len(v)} keys: {list(v.keys())[:3]}...>"
            print(f"    {k}: {v}")
        if "exception" in details:
            print(f"    exception: {details['exception']}")
        if "notify_depth" in details:
            print(f"    notify_depth: {details['notify_depth']}")
    print()
    print("=" * 72)
    print(f"FINAL EXIT: {overall}")
    print("=" * 72)
    return 0 if overall == "PASS" else 1


# ── Driver ────────────────────────────────────────────────────────────────


def main() -> int:
    print("=" * 72)
    print("watch_mission incident-replay acceptance test")
    print(f"repo_root = {REPO_ROOT}")
    print(f"caller    = {CALLER}")
    print(f"watchdog  = {WATCHDOG_SECONDS}s")
    print("=" * 72)
    print()

    scenarios = [
        ("R1", run_r1),
        ("R2", run_r2),
        ("R3", run_r3),
        ("R4", run_r4),
    ]

    for name, fn in scenarios:
        _stamp(f"Starting scenario {name}")
        db_path = Path(f"/tmp/mw-replay/r{name[-1]}.sqlite")
        # Ensure clean state per scenario.
        for ext in ("", "-wal", "-shm"):
            p = Path(str(db_path) + ext)
            if p.exists():
                p.unlink()
        verdict, details = fn(db_path)
        SCENARIO_RESULTS[name] = verdict
        SCENARIO_DETAILS[name] = details
        _stamp(f"Scenario {name}: {verdict}")

    # Dump full JSON evidence for the RESULTS report.
    evidence_path = Path("/tmp/mw-replay/evidence.json")
    evidence = {
        "scenarios": SCENARIO_RESULTS,
        "details": SCENARIO_DETAILS,
        "runtime_seconds": elapsed(),
        "watchdog_seconds": WATCHDOG_SECONDS,
    }
    evidence_path.write_text(json.dumps(evidence, indent=2, default=str))
    _stamp(f"Evidence written to {evidence_path}")

    return _emit_summary()


if __name__ == "__main__":
    sys.exit(main())
