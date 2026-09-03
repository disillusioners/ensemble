"""Integration pins for the M1 S4 dead-letter binding seam.

This file ports the S4 assertions from
``/tmp/m1-gate/onpath_verify.py`` (M1 mission-class gate) into
committed integration tests so the dead-letter binding contract is
structurally protected at the three additive read surfaces the FE
consumes.

Defect pinned here
------------------

``daemon/services/work_resolver.py:1702`` (in
``WorkResolverService._mission_fields_for_instance``) binds the three
additive mission fields (``mission_id`` / ``mission_epoch`` /
``mission_terminal_reason``) via
``MissionResolver.project(instance)`` — the public passthrough whose
``dead_linked`` parameter defaults to ``False``. The W4-hazard branch
in :func:`MissionResolver._project` is only reached when the caller
pre-fetches the linked-JobItem DEAD-admission flag via
:meth:`MissionResolver._batch_jobitem_lookup`. The work-resolver's
single-row binding path bypasses both pre-fetches, so a DEAD linked
JobItem surfaces ``mission_terminal_reason='failed'`` (the
canonicalized ERROR instance liveness) instead of the
contract-required ``'dead_letter'``.

Per mission-class spec §8.3 line 1096 (W4 hazard): DEAD admission
overrides instance liveness for the mission-side terminal_reason.
The contract is: ``mission_terminal_reason == 'dead_letter'`` for a
JobItem in ``admission_state='dead'`` regardless of instance state.

Scenarios pinned here (mission projection always-on)
----------------------------------------------------

All three pins share the S4 fixture: an ERROR-status Instance
(canonicalizes to ``'failed'``) linked to a JobItem in
``admission_state='dead'``. The pre-fix defect surfaces ``'failed'``
on every surface; post-fix surfaces ``'dead_letter'``.

* Pin 1 — LIST (GET ``/jobs?project_id=...``)
* Pin 2 — DETAIL (GET ``/jobs/{job_id}``)
* Pin 3 — SSE (GET ``/jobs/{job_id}/events``) — asserts on the
  ``completed`` event payload (the DEAD admission means
  ``work_record.status='dead_letter'``, a terminal status, so the
  SSE emits ``connected`` → ``completed`` at T0).

Recipe discipline (non-negotiable, per task spec)
-------------------------------------------------

File-backed SQLite at ``tmp_path`` with ``NullPool`` +
``PRAGMA journal_mode=WAL`` + ``PRAGMA busy_timeout=10000``. The
``StaticPool + WriteGuardSession`` combo is FORBIDDEN per
QUARANTINE.md dependency_bus row (cross-thread lost-write hazard).

Truthmaker: real repos, real routers, real services, real
ASGITransport. Zero mocks of the code under test. (WS3: the
kill-switch ON-flip discipline was removed — mission projection is
always-on.)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Iterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

# Register every model on SQLModel.metadata BEFORE create_all — same
# discipline as the gate script's Phase 1.
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.dead_letter_repository import (
    DeadLetterRepository,
)
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.repository import TaskRepository
from daemon.routers import jobs_crud, jobs_streaming
from daemon.services.dead_letter_service import DeadLetterService
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import JobQueueService
from daemon.services.work_resolver import WorkResolverService


PROJECT_ID = "m1-s4-dead-letter-pin"


# ── File-backed SQLite fixture (BLUEPRINT recipe — NOT StaticPool) ──────────


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    """File-backed SQLite at ``tmp_path`` with NullPool + WAL + busy_timeout=10000.

    Blueprint §3 recipe. ``StaticPool + WriteGuardSession`` is
    FORBIDDEN per QUARANTINE.md dependency_bus row — the single
    shared connection trips the documented cross-thread lost-write
    hazard. A file DB with per-checkout connections mirrors the
    production concurrency shape.
    """
    db_path = tmp_path / "m1_s4_dead_letter.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_pragmas(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


# (WS3: the kill-switch ON-fixture was removed — mission projection is
# always-on; no per-test env discipline is needed anymore.)


# ── Repo + service + app wiring (mirror of onpath_verify.py Phase 3-4) ──────


@pytest.fixture
def task_repo(engine: Engine) -> TaskRepository:
    return TaskRepository(engine)


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def instance_repo(engine: Engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def resolver(
    task_repo: TaskRepository,
    job_repo: JobRepository,
    instance_repo: SQLModelInstanceRepository,
) -> WorkResolverService:
    return WorkResolverService(task_repo, job_repo, instance_repo)


@pytest.fixture
def service(
    engine: Engine,
    job_repo: JobRepository,
    resolver: WorkResolverService,
) -> JobQueueService:
    """Real JobQueueService over real repos with the resolver wired."""
    svc = JobQueueService(
        repository=job_repo,
        lock_manager=JobLockManager(LockRepository(engine)),
        queue_repo=JobQueueRepository(engine),
        instance_manager=None,  # read-only surface; pool out of scope
    )
    svc.set_work_resolver(resolver)
    return svc


@pytest.fixture
def dlq_service(job_repo: JobRepository, engine: Engine) -> DeadLetterService:
    return DeadLetterService(job_repo, DeadLetterRepository(engine))


@pytest.fixture
def app(
    service: JobQueueService,
    dlq_service: DeadLetterService,
) -> FastAPI:
    """Bare FastAPI app with BOTH jobs routers mounted and the
    services wired via ``app.dependency_overrides`` (no module-level
    DI singleton pollution across tests).

    ``jobs_streaming`` imports the SAME DI singleton from
    ``jobs_crud`` — one override covers both routers, asserted below
    so the assumption is checked not assumed.
    """
    assert jobs_streaming.get_job_queue_service is jobs_crud.get_job_queue_service
    app = FastAPI()
    app.include_router(jobs_crud.router)
    app.include_router(jobs_streaming.router)
    app.dependency_overrides[jobs_crud.get_job_queue_service] = lambda: service
    app.dependency_overrides[jobs_crud.get_dead_letter_svc] = lambda: dlq_service
    return app


# ── S4 fixture: DEAD JobItem + ERROR Instance (W4-hazard shape) ─────────────


@pytest.fixture
def s4_dead_letter_scenario(engine: Engine) -> tuple[str, str]:
    """Seed the W4-hazard fixture and return ``(job_id, instance_id)``.

    The pre-fix defect observable here: ``mission_terminal_reason`` is
    derived from the canonicalized instance liveness (ERROR → 'failed'),
    NOT from the linked JobItem's DEAD admission. The pinned
    assertion is the post-fix contract: ``'dead_letter'`` (W4 hazard —
    DEAD admission overrides instance liveness for the
    mission_terminal_reason field per §8.3 line 1096).
    """
    iid = "inst-m1-s4-target"
    jid = "job-m1-s4-dead-letter"
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                project_id=PROJECT_ID,
                status=InstanceStatus.ERROR.value,
                created_at=now_iso,
                updated_at=now_iso,
                last_activity_at=now,
                paused_at=None,
                parent_id=None,
            )
        )
        s.commit()

    with Session(engine) as s:
        s.add(
            JobItem(
                job_id=jid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="m1 s4 dead-letter seed",
                source="api",
                project_id=PROJECT_ID,
                priority=5,
                admission_state=AdmissionState.DEAD.value,
                instance_id=iid,
                created_at=now_iso,
                job_metadata={},
                terminal_reason="dead_letter",
                job_type="task",
            )
        )
        s.commit()

    return jid, iid


# ── Pin 1: LIST surface ────────────────────────────────────────────────────


async def test_s4_dead_letter_list_surface_surfaces_dead_letter(
    app: FastAPI,
    s4_dead_letter_scenario: tuple[str, str],
) -> None:
    """GET ``/jobs?project_id=...`` surfaces ``mission_terminal_reason='dead_letter'``.

    The LIST route resolves through
    ``JobQueueService.list_jobs`` → ``WorkResolverService.list_work``
    → ``_job_to_record`` → ``_mission_fields_for_instance`` — the
    binding seam at ``work_resolver.py:1702`` is on this path.
    Pre-fix observes ``'failed'`` (the canonicalized ERROR instance
    liveness). Post-fix observes ``'dead_letter'`` (the W4-hazard
    branch reached via the pre-fetched DEAD-admission flag).

    The liveness branch (W4) is also exercised: the underlying
    instance liveness would yield ``'failed'`` without the fix; the
    dead-link overrides it to ``'dead_letter'`` per §8.3 line 1096.
    """
    jid, iid = s4_dead_letter_scenario

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        timeout=httpx.Timeout(10.0),
    ) as client:
        resp = await client.get("/jobs", params={"project_id": PROJECT_ID})

    assert resp.status_code == 200, f"LIST HTTP {resp.status_code}: {resp.text[:200]}"
    body = resp.json()

    s4_row = None
    for row in body.get("jobs", []):
        if row.get("job_id") == jid:
            s4_row = row
            break
    assert s4_row is not None, (
        f"LIST missing job_id={jid}; present ids="
        f"{[r.get('job_id') for r in body.get('jobs', [])]}"
    )

    # M1 additive keys surface verbatim (always-on since WS3).
    assert "mission_id" in s4_row, (
        f"LIST row missing 'mission_id' (always-on contract); "
        f"row keys={sorted(s4_row.keys())}"
    )
    assert "mission_epoch" in s4_row, (
        f"LIST row missing 'mission_epoch'; row keys={sorted(s4_row.keys())}"
    )
    assert "mission_terminal_reason" in s4_row, (
        f"LIST row missing 'mission_terminal_reason'; row keys={sorted(s4_row.keys())}"
    )

    # mission_id == instance_id per §3 identity contract.
    assert s4_row["mission_id"] == iid
    # Constant-1 epoch contract (per-epoch timestamps are M4(ii) scope).
    assert s4_row["mission_epoch"] == 1

    # W4 hazard assertion — the headline pin. Pre-fix observes 'failed'
    # (canonicalized ERROR); post-fix must observe 'dead_letter' (W4).
    assert s4_row["mission_terminal_reason"] == "dead_letter", (
        f"LIST mission_terminal_reason must be 'dead_letter' per W4 hazard "
        f"(DEAD admission overrides ERROR instance liveness per spec §8.3 "
        f"line 1096); got {s4_row['mission_terminal_reason']!r}. "
        f"Defect location: daemon/services/work_resolver.py:1702 "
        f"(_mission_fields_for_instance binding seam calls "
        f"resolver.project() which defaults dead_linked=False — the W4 "
        f"branch in MissionResolver._project is unreachable without the "
        f"pre-fetched dead_linked flag from _batch_jobitem_lookup)."
    )


# ── Pin 2: DETAIL surface ──────────────────────────────────────────────────


async def test_s4_dead_letter_detail_surface_surfaces_dead_letter(
    app: FastAPI,
    s4_dead_letter_scenario: tuple[str, str],
) -> None:
    """GET ``/jobs/{job_id}`` surfaces ``mission_terminal_reason='dead_letter'``.

    The DETAIL route resolves through ``JobQueueService.get_job`` →
    ``WorkQueueService.get_work`` → ``_job_to_record`` → the same
    binding seam at ``work_resolver.py:1702``. The fix has to cover
    this single-row path too (the list and single-row paths share
    ``_mission_fields_for_instance``).
    """
    jid, iid = s4_dead_letter_scenario

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        timeout=httpx.Timeout(10.0),
    ) as client:
        resp = await client.get(f"/jobs/{jid}")

    assert resp.status_code == 200, f"DETAIL HTTP {resp.status_code}: {resp.text[:200]}"
    detail = resp.json()

    # M1 additive keys present (always-on since WS3).
    assert "mission_id" in detail
    assert "mission_epoch" in detail
    assert "mission_terminal_reason" in detail

    assert detail["mission_id"] == iid
    assert detail["mission_epoch"] == 1
    assert detail["mission_terminal_reason"] == "dead_letter", (
        f"DETAIL mission_terminal_reason must be 'dead_letter' per W4; "
        f"got {detail['mission_terminal_reason']!r}"
    )


# ── Pin 3: SSE surface ─────────────────────────────────────────────────────


async def test_s4_dead_letter_sse_completed_event_surfaces_dead_letter(
    app: FastAPI,
    s4_dead_letter_scenario: tuple[str, str],
) -> None:
    """GET ``/jobs/{job_id}/events`` emits ``mission_terminal_reason='dead_letter'``
    in the ``completed`` event payload.

    The DEAD admission + ``terminal_reason='dead_letter'`` puts the
    WorkRecord at ``status='dead_letter'`` (terminal), so the SSE
    generator emits ``connected`` followed by ``completed`` at T0 —
    no polling required. Both payloads carry the mission_* fields
    (always-on since WS3 — the M1 contract with the kill-switch
    removed).

    The completed-event assertion is the W4-hazard pin on the SSE
    surface: a DEAD linked JobItem must surface
    ``mission_terminal_reason='dead_letter'`` in the wire payload,
    not the canonicalized ERROR instance liveness value 'failed'.
    """
    jid, iid = s4_dead_letter_scenario

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        timeout=httpx.Timeout(10.0),
    ) as client:
        async with client.stream("GET", f"/jobs/{jid}/events") as resp:
            assert resp.status_code == 200, f"SSE HTTP {resp.status_code}"
            events = await _read_sse_events(resp, max_events=2)
            await resp.aclose()

    names = [e["event"] for e in events]
    assert names == ["connected", "completed"], (
        f"SSE expected ['connected', 'completed'] for terminal-state DEAD work; "
        f"got {names}"
    )

    connected = events[0]["data"]
    completed = events[1]["data"]

    # Both payloads surface mission_* keys (always-on).
    assert "mission_id" in connected
    assert "mission_terminal_reason" in connected
    assert "mission_id" in completed
    assert "mission_terminal_reason" in completed

    assert connected["mission_id"] == iid
    assert completed["mission_id"] == iid

    # W4 hazard: completed event surfaces 'dead_letter'.
    assert completed["mission_terminal_reason"] == "dead_letter", (
        f"SSE 'completed' event mission_terminal_reason must be 'dead_letter' "
        f"per W4 hazard (DEAD admission overrides ERROR instance liveness); "
        f"got {completed['mission_terminal_reason']!r}. "
        f"Defect location: daemon/services/work_resolver.py:1702."
    )
    # The connected event (status='dead_letter' at T0) also carries the
    # mission_terminal_reason — pin the W4 value here too so a future
    # regression that ONLY fixes the completed branch is caught.
    assert connected["mission_terminal_reason"] == "dead_letter", (
        f"SSE 'connected' event mission_terminal_reason must be 'dead_letter' "
        f"per W4; got {connected['mission_terminal_reason']!r}"
    )


# ── Liveness-branch pin (asserts the W4 still holds) ───────────────────────


async def test_s4_dead_letter_liveness_branch_yields_failed_when_no_dead_link(
    app: FastAPI,
    engine: Engine,
) -> None:
    """Companion pin — INSTANCE liveness branch yields 'failed' when NO dead-link.

    The spec §8.3 line 1096 contract is that DEAD admission OVERRIDES
    instance liveness for the mission_terminal_reason field. This
    pin nails down the OTHER side: when there is NO DEAD link, the
    mission_terminal_reason IS the canonicalized instance liveness
    (ERROR → 'failed'). Without this pin, the pre-fix defect ('failed'
    for a DEAD link) would coincidentally match the liveness-branch
    contract — and the regression test wouldn't catch a fix that
    silently dropped the W4 branch.
    """
    iid = "inst-m1-s4-control-liveness"
    jid = "job-m1-s4-control-liveness"
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                project_id=PROJECT_ID,
                status=InstanceStatus.ERROR.value,
                created_at=now_iso,
                updated_at=now_iso,
                last_activity_at=now,
                paused_at=None,
                parent_id=None,
            )
        )
        s.commit()

    with Session(engine) as s:
        s.add(
            JobItem(
                job_id=jid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="m1 s4 liveness-branch control",
                source="api",
                project_id=PROJECT_ID,
                priority=5,
                # NOT dead — admission_state='done' so no W4 override.
                admission_state=AdmissionState.DONE.value,
                instance_id=iid,
                created_at=now_iso,
                job_metadata={},
                terminal_reason="failed",
                job_type="task",
            )
        )
        s.commit()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        timeout=httpx.Timeout(10.0),
    ) as client:
        resp = await client.get(f"/jobs/{jid}")

    assert resp.status_code == 200
    detail = resp.json()

    # No DEAD link → liveness branch stands → 'failed' from ERROR.
    assert detail["mission_terminal_reason"] == "failed", (
        f"Control: with admission_state='done' (no DEAD link), the "
        f"liveness branch MUST yield 'failed' (canonicalized ERROR); "
        f"got {detail['mission_terminal_reason']!r}. If this is 'dead_letter', "
        f"the W4 branch is firing on the wrong path (no dead link exists)."
    )


# ── SSE wire-format helper (mirrors tests/unit/routers/test_jobs_streaming_resolver.py) ─


async def _read_sse_events(response, *, max_events: int = 4) -> list[dict]:
    """Parse SSE ``event:`` / ``data:`` pairs into a list of dicts.

    Async; caller breaks out of the loop once enough events have
    been collected, then closes the response to terminate the
    infinite SSE stream.
    """
    events: list[dict] = []
    current: dict = {}
    async for line in response.aiter_lines():
        if not line:
            if current.get("event") and current.get("data"):
                events.append(current)
                current = {}
                if len(events) >= max_events:
                    break
            continue
        if line.startswith("event:"):
            current["event"] = line[len("event:"):].strip()
        elif line.startswith("data:"):
            payload = line[len("data:"):].strip()
            try:
                current["data"] = json.loads(payload)
            except json.JSONDecodeError:
                current["data"] = payload
    # Flush trailing event without blank terminator (defensive).
    if current.get("event") and current.get("data"):
        events.append(current)
    return events
