"""Integration pin tests for Fix C — the read-model split at the ROUTER
and SSE surfaces (28c6421b scenario, runtime-proven shape).

The resolver-level split contract is pinned by
``tests/unit/services/test_fix_c_read_model_split.py`` (committed).
This file pins the SAME contract one layer up, at the two wire
surfaces the frontend actually consumes:

    * ``GET /jobs/{job_id}`` (``routers/jobs_crud.py`` → ``JobResponse``)
    * ``GET /jobs/{job_id}/events`` (``routers/jobs_streaming.py`` →
      ``_ResolvedWork`` payloads on ``connected`` / ``status_update`` /
      ``completed`` events)

plus the MISSION-ROW complement, so the mission/mirror split cannot
silently invert.

The exact incident reproduced here (28c6421b): a mirror JobItem
(``job_type='message'``) reaches ``admission_state='done'`` with
``terminal_reason='completed'`` (Fix B's inline idempotent transition
at T0) while the linked mission instance keeps living in
``status='waiting_children'``. The read must carry BOTH answers —
``status='completed'`` (the receipt IS true) and
``mission_liveness='processing'`` (the mission is still alive) — so
the false-"everything finished" read is impossible at every surface.

Assertions mirror the runtime-proven gate evidence
(``/tmp/fixc-gate/d1_28c6421b_runtime.py``, 2026-09-02) and the
contract in ``docs/job-task-system.md`` §8.2. Real repos, real
FastAPI routes via TestClient / httpx ASGITransport — no
kwarg-substring asserts anywhere.

Harness notes
-------------

StaticPool in-memory SQLite recipe, same as
``tests/unit/services/test_fix_c_read_model_split.py`` and
``tests/unit/routers/test_jobs_streaming_resolver.py``. Both routers
are mounted on one bare ``FastAPI()`` app with the services wired via
``app.dependency_overrides`` (scoped to the app — no module-level DI
singleton pollution across tests).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Register every model on ``SQLModel.metadata`` BEFORE ``create_all`` —
# mirrors the harness in ``tests/unit/services/test_work_resolver.py``.
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance
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


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool + FK on)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


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
    job_repo: JobRepository,
    resolver: WorkResolverService,
    engine: Engine,
) -> JobQueueService:
    """JobQueueService over real repos with the resolver wired (the
    production read path). Read-only surface: the instance pool is
    out of scope.
    """
    svc = JobQueueService(
        repository=job_repo,
        lock_manager=JobLockManager(LockRepository(engine)),
        queue_repo=JobQueueRepository(engine),
        instance_manager=None,
    )
    svc.set_work_resolver(resolver)
    return svc


@pytest.fixture
def dlq_service(job_repo: JobRepository, engine: Engine) -> DeadLetterService:
    return DeadLetterService(job_repo, DeadLetterRepository(engine))


@pytest.fixture
def app(service: JobQueueService, dlq_service: DeadLetterService) -> FastAPI:
    """Bare FastAPI app carrying BOTH jobs routers with the services
    wired via ``app.dependency_overrides``.

    ``jobs_streaming`` imports the SAME DI singleton object from
    ``jobs_crud``, so one override covers both routers — asserted
    below so the assumption is checked, not assumed.
    """
    assert jobs_streaming.get_job_queue_service is jobs_crud.get_job_queue_service
    app = FastAPI()
    app.include_router(jobs_crud.router)
    app.include_router(jobs_streaming.router)
    app.dependency_overrides[jobs_crud.get_job_queue_service] = lambda: service
    app.dependency_overrides[jobs_crud.get_dead_letter_svc] = lambda: dlq_service
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ─── Seed helpers ───────────────────────────────────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    agent_id: str = "developer",
    project_id: str | None = "test-project",
    status: str = "running",
    recent_activity: bool = True,
) -> str:
    """Insert an Instance row. Returns ``instance_id``.

    ``recent_activity=True`` stamps ``last_activity_at`` 2 minutes ago
    — the live-mission heartbeat shape from the 28c6421b scenario.
    """
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                agent_name=agent_id,
                project_id=project_id,
                status=status,
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
                last_activity_at=(
                    now - timedelta(minutes=2) if recent_activity else None
                ),
                paused_at=None,
                parent_id=None,
            )
        )
        s.commit()
    return iid


def _seed_job(
    engine: Engine,
    *,
    job_id: str | None = None,
    agent_id: str = "developer",
    instance_id: str | None = None,
    admission_state: str = AdmissionState.QUEUED.value,
    terminal_reason: str | None = None,
    job_type: str = "task",
) -> str:
    """Insert a JobItem row. Returns ``job_id``."""
    jid = job_id or str(uuid.uuid4())
    created = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            JobItem(
                job_id=jid,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                message="fix-c surface pin message",
                source="api",
                project_id="test-project",
                priority=5,
                admission_state=admission_state,
                instance_id=instance_id,
                created_at=created,
                job_metadata={},
                terminal_reason=terminal_reason,
                job_type=job_type,
            )
        )
        s.commit()
    return jid


@pytest.fixture
def mirror_scenario(engine: Engine) -> tuple[str, str]:
    """The 28c6421b read: DONE mirror (``job_type='message'``,
    ``terminal_reason='completed'``) beside a live mission instance in
    ``status='waiting_children'``. Returns ``(job_id, instance_id)``.
    """
    iid = _seed_instance(
        engine, instance_id="inst-live-parent", status="waiting_children"
    )
    jid = _seed_job(
        engine,
        instance_id=iid,
        admission_state=AdmissionState.DONE.value,
        terminal_reason="completed",
        job_type="message",
    )
    return jid, iid


@pytest.fixture
def mission_scenario(engine: Engine) -> tuple[str, str]:
    """The mission-row complement: an ACTIVE task-type JobItem (the
    JobItem IS the instance's lifecycle proxy) linked to a live
    instance in ``status='waiting_children'``. Returns
    ``(job_id, instance_id)``.
    """
    iid = _seed_instance(
        engine, instance_id="inst-mission-live", status="waiting_children"
    )
    jid = _seed_job(
        engine,
        instance_id=iid,
        admission_state=AdmissionState.ACTIVE.value,
        job_type="task",
    )
    return jid, iid


# ─── SSE helpers ────────────────────────────────────────────────────────────


async def _read_sse_events(response, *, max_events: int = 4) -> list[dict]:
    """Parse SSE ``event:`` / ``data:`` pairs into a list of dicts.

    Recipe lifted from
    ``tests/unit/routers/test_jobs_streaming_resolver.py`` — breaks out
    after ``max_events`` so closing the response terminates the stream.
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
    if current.get("event") and current.get("data"):
        events.append(current)
    return events


# ─── Pin 1: ROUTER surface — the 28c6421b mirror read ──────────────────────


class TestRouterSurface28c6421b:
    """``GET /jobs/{job_id}`` must carry the split: the DONE mirror's
    receipt status AND the live mission's liveness — HTTP 200, no
    false-"everything finished" collapse.
    """

    def test_done_mirror_on_live_mission_carries_split(
        self, client: TestClient, mirror_scenario: tuple[str, str]
    ) -> None:
        jid, iid = mirror_scenario

        resp = client.get(f"/jobs/{jid}")

        assert resp.status_code == 200
        body = resp.json()
        # Additive Fix-C keys are on the wire.
        assert "job_type" in body
        assert "mission_liveness" in body
        # The receipt — the mirror's own terminal truth.
        assert body["status"] == "completed"
        # The discriminator.
        assert body["job_type"] == "message"
        # The mission is still alive — the split that kills the
        # 28c6421b false-"everything finished" read.
        assert body["mission_liveness"] == "processing", (
            f"expected mission_liveness='processing' for a DONE mirror on a "
            f"waiting_children mission, got {body.get('mission_liveness')!r}; "
            f"the router surface has re-collapsed the split."
        )
        # The liveness names THE linked mission instance.
        assert body["instance_id"] == iid


# ─── Pin 2: SSE surface — same fixture, same values ────────────────────────


class TestSSESurface28c6421b:
    """The ``_ResolvedWork`` payloads the SSE route emits (``connected``
    / ``status_update`` / ``completed``) carry all three keys with the
    same values as the router surface.
    """

    async def test_sse_payloads_carry_split_keys(
        self, app: FastAPI, service: JobQueueService,
        mirror_scenario: tuple[str, str],
    ) -> None:
        jid, _iid = mirror_scenario

        # L3a — the exact payload-construction path the route calls:
        #   route → _resolve(service, job_id, ...) → _ResolvedWork
        #         → .to_payload / .to_completed_payload
        resolved = await jobs_streaming._resolve(service, jid, True)
        assert resolved is not None, "SSE route would 404 — fixture broken"
        connected_payload = resolved.to_payload(work_id=jid)
        completed_payload = resolved.to_completed_payload(work_id=jid)
        for payload in (connected_payload, completed_payload):
            assert payload["status"] == "completed"
            assert payload["job_type"] == "message"
            assert payload["mission_liveness"] == "processing"

        # L3b — the FULL SSE route over the ASGI transport.
        async def _stream():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                timeout=httpx.Timeout(30.0),
            ) as async_client:
                async with async_client.stream(
                    "GET", f"/jobs/{jid}/events"
                ) as response:
                    assert response.status_code == 200
                    return await _read_sse_events(response, max_events=2)

        # The mirror IS terminal → the route emits connected + completed
        # and closes.
        events = await _stream()
        names = [e["event"] for e in events]
        assert names == ["connected", "completed"]

        for event_name, data in zip(names, [e["data"] for e in events]):
            assert data["status"] == "completed", f"{event_name} payload"
            assert data["job_type"] == "message", f"{event_name} payload"
            assert data["mission_liveness"] == "processing", (
                f"{event_name} payload lost mission_liveness — the SSE "
                f"surface has re-collapsed the split."
            )


# ─── Pin 3: MISSION-ROW complement — the split must not invert ─────────────


class TestMissionRowComplement:
    """For a mission (task-type) JobItem the row's own ``status`` IS the
    liveness signal (Phase 1, Job as Queue Proxy) — so the router and
    SSE surfaces must render ``status`` = the instance-proxy status,
    ``job_type='task'``, and ``mission_liveness=None``. Without this
    complement the split could silently invert (mission rendered as a
    receipt, or mirrors rendered as missions).
    """

    def test_active_mission_router_shape(
        self, client: TestClient, mission_scenario: tuple[str, str]
    ) -> None:
        jid, iid = mission_scenario

        resp = client.get(f"/jobs/{jid}")

        assert resp.status_code == 200
        body = resp.json()
        # Instance-proxy status: waiting_children canonicalizes to
        # 'processing' — NOT a receipt value.
        assert body["status"] == "processing"
        assert body["job_type"] == "task"
        # Mission rows do NOT carry liveness — the field would be
        # redundant with status (§8.2).
        assert body["mission_liveness"] is None, (
            f"expected mission_liveness=None for a mission row, got "
            f"{body.get('mission_liveness')!r}; the mission/mirror split "
            f"has inverted."
        )
        assert body["instance_id"] == iid

    async def test_active_mission_sse_connected_shape(
        self, service: JobQueueService,
        mission_scenario: tuple[str, str],
    ) -> None:
        jid, _iid = mission_scenario

        # Pin the SSE payload at the exact construction the route feeds
        # its ``connected`` / ``status_update`` events:
        #   route → _resolve(service, job_id, ...) → _ResolvedWork
        #         → .to_payload  (json-dumped into the event data)
        # The full route→wire path is proven for this app wiring by
        # ``test_sse_payloads_carry_split_keys`` (mirror fixture). The
        # mission row is NON-terminal, so its route stream never self-
        # terminates and httpx ASGITransport does not propagate client
        # disconnects (see ``tests/integration/test_workspace_sse.py``
        # ``_StreamTimeoutASGI`` for the heavy bounding recipe) — a
        # helper-level pin is the proportionate shape check here.
        resolved = await jobs_streaming._resolve(service, jid, True)
        assert resolved is not None, "SSE route would 404 — fixture broken"
        data = resolved.to_payload(work_id=jid)
        assert data["status"] == "processing"
        assert data["job_type"] == "task"
        # Key present with None — the stable wire shape (§8.2: both
        # fields are emitted on EVERY SSE payload).
        assert "mission_liveness" in data
        assert data["mission_liveness"] is None
