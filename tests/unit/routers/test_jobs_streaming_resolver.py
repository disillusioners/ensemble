"""Unit tests for the SSE rewire on the virtual job management surface.

Phase 2 (Batch 4b) of ``feature/virtual-job-management-surface`` —
``stream_job_events`` now branches on the
``use_virtual_job_resolver`` config flag:

* ``OFF`` (legacy): polls ``JobQueueService.get_job`` → emits JobItem-shaped
  payloads (the original wire format).
* ``ON``  (default): polls ``JobQueueService.get_work`` → emits the same
  JSON keys but values come from a :class:`WorkRecord` (``record.error``
  is mapped back onto ``error_message``, ``queue_id`` collapses to
  ``None``).

This file pins the wire-format compatibility: both branches must emit
identical JSON keys for a terminal-state work unit so the frontend
consumer does not need a corresponding change.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import JobItem, JobQueue, AdmissionState
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.routers.jobs_streaming import router as streaming_router
from daemon.services.job_queue_service import JobQueueService
from daemon.services.work_resolver import WorkResolverService



# >>> test-local status_to_admission (Phase 4 cleanup) <<<
# Phase 4 cleanup removed ``status_to_admission`` from
# ``daemon.repositories.job_queue.models``. Redefined here for test
# seeds that derive ``admission_state`` from a ``status`` value.
def status_to_admission(status):  # noqa: ANN001,ANN201
    return {
        "pending": "queued",
        "processing": "active",
        "paused": "active",
        "completed": "done",
        "failed": "done",
        "cancelled": "done",
        "dead_letter": "dead",
    }.get(status, "queued")


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite engine (StaticPool + FK on), mirrors Phase 1 tests."""
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
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def task_repo(engine: Engine) -> TaskRepository:
    return TaskRepository(engine)


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


# ─── Service fixtures (with and without resolver wired) ─────────────────────


@pytest.fixture
def service_with_resolver(
    job_repo: JobRepository,
    resolver: WorkResolverService,
) -> JobQueueService:
    """JobQueueService with the resolver wired (the production default)."""
    svc = JobQueueService(
        repository=job_repo,
        lock_manager=MagicMock(),
        queue_repo=MagicMock(),
        instance_manager=MagicMock(),
    )
    svc.set_work_resolver(resolver)
    return svc


@pytest.fixture
def service_without_resolver(
    job_repo: JobRepository,
    resolver: WorkResolverService,
) -> JobQueueService:
    """Legacy JobQueueService — wired with the resolver but flag forced OFF.

    Phase 1 (Job as Queue Proxy): the legacy JobItem-direct branch
    in ``jobs_streaming._resolve`` has been removed. Both the ON and
    OFF flag paths now route through ``service.get_work`` →
    ``WorkResolverService.resolve_work``. Wiring the resolver in
    this fixture is therefore required — without it ``get_work``
    returns ``None`` and every endpoint call surfaces a 404.
    """
    svc = JobQueueService(
        repository=job_repo,
        lock_manager=MagicMock(),
        queue_repo=MagicMock(),
        instance_manager=MagicMock(),
    )
    svc.set_work_resolver(resolver)
    return svc


# ─── App fixtures (one per branch) ───────────────────────────────────────────


@pytest.fixture
def app_resolver_on(
    service_with_resolver: JobQueueService,
) -> FastAPI:
    """FastAPI app wired with ``use_virtual_job_resolver=True``."""
    app = FastAPI()
    app.include_router(streaming_router, prefix="/api")
    # Stash a manager whose config advertises the resolver flag = ON.
    app.state.manager = SimpleNamespace(
        config=SimpleNamespace(
            job_system=SimpleNamespace(use_virtual_job_resolver=True),
        ),
    )
    # Override the DI dependency so the router picks up our wired service.
    from daemon.routers.jobs_streaming import get_job_queue_service

    app.dependency_overrides[get_job_queue_service] = lambda: service_with_resolver
    return app


@pytest.fixture
def app_resolver_off(
    service_without_resolver: JobQueueService,
) -> FastAPI:
    """FastAPI app wired with ``use_virtual_job_resolver=False``."""
    app = FastAPI()
    app.include_router(streaming_router, prefix="/api")
    app.state.manager = SimpleNamespace(
        config=SimpleNamespace(
            job_system=SimpleNamespace(use_virtual_job_resolver=False),
        ),
    )
    from daemon.routers.jobs_streaming import get_job_queue_service

    app.dependency_overrides[get_job_queue_service] = lambda: service_without_resolver
    return app


@pytest.fixture
def client_resolver_on(app_resolver_on: FastAPI) -> TestClient:
    return TestClient(app_resolver_on)


@pytest.fixture
def client_resolver_off(app_resolver_off: FastAPI) -> TestClient:
    return TestClient(app_resolver_off)


# ─── Seed helpers ────────────────────────────────────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    agent_id: str = "developer",
    project_id: str | None = "test-project",
    status: str = "running",
) -> str:
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        inst = Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            agent_name=agent_id,
            project_id=project_id,
            status=status,
            created_at=now_iso,
            updated_at=now_iso,
            paused_at=None,
        )
        s.add(inst)
        s.commit()
    return iid


def _seed_completed_job(
    engine: Engine,
    *,
    job_id: str | None = None,
    queue_id: str | None = None,
    instance_id: str | None = None,
) -> str:
    """Insert a JobItem already in the terminal ``completed`` state.

    If ``queue_id`` is set, also inserts a matching ``JobQueue`` row so
    the FK constraint on ``job_queue_items.queue_id`` is satisfied.
    If ``instance_id`` is set, stamps the JobItem with that backing
    instance — required for the M1 ON-path projection test (the
    ``mission_id`` field resolves to ``instance_id`` per spec §3).
    """
    jid = job_id or str(uuid.uuid4())
    with Session(engine) as s:
        if queue_id is not None:
            queue = JobQueue(
                queue_id=queue_id,
                project_id="test-project",
                queue_name=queue_id,
                queue_name_lower=queue_id,
                queue_type="fifo",
                concurrency_limit=1,
                is_system=False,
                is_paused=False,
                created_at=datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            s.add(queue)
            s.commit()
        job = JobItem(
            job_id=jid,
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="m",
            source="api",
            project_id="test-project",
            priority=5,

            admission_state=AdmissionState.DONE.value,
            instance_id=instance_id,
            queue_id=queue_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            job_metadata={},
        )
        s.add(job)
        s.commit()
    return jid


def _seed_completed_task(
    engine: Engine,
    *,
    work_id: str | None = None,
    result: str = "task done",
    error: str | None = None,
) -> str:
    """Insert a Task already in the terminal ``completed`` state.

    ``Task.instance_id`` is NOT NULL, so we always seed a matching
    Instance row first.
    """
    wid = work_id or str(uuid.uuid4())
    iid = _seed_instance(engine)
    with Session(engine) as s:
        task = Task(
            work_id=wid,
            task_type="process_message",
            instance_id=iid,
            message_id=None,
            status=TaskStatus.COMPLETED.value,
            result=result,
            error=error,
            created_at=datetime.now(timezone.utc),
        )
        s.add(task)
        s.commit()
    return wid


# ─── Wire-format helpers ─────────────────────────────────────────────────────


async def _read_sse_events(response, *, max_events: int = 4) -> list[dict]:
    """Parse SSE ``event:`` / ``data:`` pairs into a list of dicts.

    Uses ``response.aiter_lines()`` (async) so the caller can break
    out of the loop once enough events have been collected, then close
    the response to terminate the infinite SSE stream.
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


# ─── Tests ──────────────────────────────────────────────────────────────────


class TestStreamJobEventsResolverOff:
    """Legacy branch — routes through the resolver even with the flag OFF.

    Phase 1 (Job as Queue Proxy): the legacy ``_ResolvedWork.from_job``
    JobItem-direct branch has been removed. The OFF path now goes
    through the same resolver call as the ON path; both branches
    produce identical wire payloads (queue_id collapses to ``None``
    on the unified WorkRecord view-model). The flag remains in the
    config schema for Phase 7 cleanup but it no longer changes the
    data path.
    """

    async def test_completed_job_emits_terminal_events_with_legacy_fields(
        self,
        engine: Engine,
        app_resolver_off: FastAPI,
        job_repo: JobRepository,
    ):
        """A completed JobItem must stream ``connected`` + ``completed`` with
        ``result_summary`` / ``error_message`` populated.

        Phase 1: ``queue_id`` collapses to ``None`` on the resolver path
        (the WorkRecord does not surface JobItem queue affinity).
        ``result_summary`` and ``error_message`` round-trip via the
        WorkRecord (``record.result_summary`` / ``record.error``).
        """
        jid = _seed_completed_job(engine, queue_id="queue-xyz")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_resolver_off),
            base_url="http://test",
            timeout=httpx.Timeout(10.0),
        ) as async_client:
            async with async_client.stream(
                "GET", f"/api/jobs/{jid}/events"
            ) as response:
                assert response.status_code == 200
                events = await _read_sse_events(response, max_events=2)
                await response.aclose()

        names = [e["event"] for e in events]
        assert names == ["connected", "completed"]

        connected = events[0]["data"]
        assert connected["job_id"] == jid
        assert connected["status"] == "completed"
        # Phase 1 (Job as Queue Proxy): ``queue_id`` collapses to
        # ``None`` on the resolver path even with the flag OFF. The
        # legacy "verbatim queue_id" assertion no longer applies
        # because both branches now route through the resolver.
        assert connected["queue_id"] is None

        completed = events[1]["data"]
        assert completed["job_id"] == jid
        assert completed["status"] == "completed"
        # Phase 5: ``result_summary`` is ``None`` for JobItem-backed
        # records because the mirror columns were dropped. The
        # resolver sources it from Instance/Task, not the JobItem.
        assert completed["result_summary"] is None
        assert completed["error_message"] is None
        assert completed["queue_id"] is None

    def test_unknown_work_id_returns_404(
        self,
        client_resolver_off: TestClient,
    ):
        response = client_resolver_off.get("/api/jobs/no-such-id/events")
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "Job not found"

    async def test_sse_content_type_header(
        self,
        engine: Engine,
        app_resolver_off: FastAPI,
    ):
        jid = _seed_completed_job(engine)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_resolver_off),
            base_url="http://test",
            timeout=httpx.Timeout(10.0),
        ) as async_client:
            async with async_client.stream(
                "GET", f"/api/jobs/{jid}/events"
            ) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")
                await response.aclose()


class TestStreamJobEventsResolverOn:
    """New branch — polls ``WorkRecord`` via ``service.get_work``.

    Wire format must remain backward compatible:

    * ``result_summary`` and ``error_message`` still appear in the
      ``completed`` payload (mapped from ``WorkRecord.error``).
    * ``queue_id`` collapses to ``None`` for ``WorkRecord``-backed rows
      because the unified view-model does not carry queue affinity.
    * Status is canonical (Task ``running`` → ``processing``,
      JobItem ``processing`` stays ``processing``).
    """

    async def test_completed_job_via_resolver_emits_terminal_events(
        self,
        engine: Engine,
        app_resolver_on: FastAPI,
    ):
        """JobItem-backed work_id resolves through the resolver."""
        jid = _seed_completed_job(engine, queue_id="queue-original")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_resolver_on),
            base_url="http://test",
            timeout=httpx.Timeout(10.0),
        ) as async_client:
            async with async_client.stream(
                "GET", f"/api/jobs/{jid}/events"
            ) as response:
                assert response.status_code == 200
                events = await _read_sse_events(response, max_events=2)
                await response.aclose()

        names = [e["event"] for e in events]
        assert names == ["connected", "completed"]

        completed = events[1]["data"]
        # Wire-format contract: same keys as the legacy branch, plus
        # the Fix C additive split-semantics fields, the M1 additive
        # mission projection fields (always-on since WS3 — the
        # kill-switch was removed), AND the M2 anti-trap guardrails
        # (``outcome`` + ``mission_ref``, contract draft §3, additive
        # — older clients ignore the extra keys). Consumers that
        # branch on these keys (``job_type``) can distinguish mission
        # vs mirror rows; consumers that ignore them are unaffected
        # (backward-compatible additive contract). See
        # ``test_resolver_emits_mission_fields_always_on`` for the
        # populated-value variant.
        assert set(completed.keys()) == {
            "job_id",
            "status",
            "result_summary",
            "error_message",
            "queue_id",
            "job_type",
            "mission_liveness",
            "mission_id",
            "mission_epoch",
            "mission_terminal_reason",
            # M2 (mission-class) — anti-trap guardrails. ``outcome``
            # stays ``None`` on transport (draft §3.2) and
            # ``mission_ref`` carries the cross-reference payload
            # (draft §3.3).
            "outcome",
            "mission_ref",
        }
        assert completed["job_id"] == jid
        assert completed["status"] == "completed"
        # Phase 5: ``result_summary`` is ``None`` for JobItem-backed
        # records (mirror columns dropped). The resolver does not
        # derive it from ``admission_state``.
        assert completed["result_summary"] is None
        # ``WorkRecord`` does not surface JobItem.queue_id, so the SSE
        # payload collapses it to ``None`` instead of leaking an empty
        # string. The frontend accepts either null or a real value.
        assert completed["queue_id"] is None
        # M1 — additive mission fields are ALWAYS on the wire now
        # (always-on since WS3). This row has no linked Instance, so
        # the values are ``None`` — presence of the keys (not
        # non-null values) is the wire contract. See
        # ``_ResolvedWork.to_payload`` and ``JobResponse._serialize``.
        assert "mission_id" in completed
        assert "mission_epoch" in completed
        assert "mission_terminal_reason" in completed
        assert completed["mission_id"] is None
        assert completed["mission_epoch"] is None
        assert completed["mission_terminal_reason"] is None

    async def test_completed_task_via_resolver_emits_terminal_events(
        self,
        engine: Engine,
        app_resolver_on: FastAPI,
    ):
        """Task-backed work_id resolves through the resolver too.

        This is the headline Batch 4b behaviour: a Task row never
        surfaced through the SSE endpoint before, but the rewire means
        the same ``/api/jobs/{work_id}/events`` URL now streams Task
        lifecycle events to any caller that knows the work_id.
        """
        wid = _seed_completed_task(engine, result="task finished", error=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_resolver_on),
            base_url="http://test",
            timeout=httpx.Timeout(10.0),
        ) as async_client:
            async with async_client.stream(
                "GET", f"/api/jobs/{wid}/events"
            ) as response:
                assert response.status_code == 200
                events = await _read_sse_events(response, max_events=2)
                await response.aclose()

        names = [e["event"] for e in events]
        assert names == ["connected", "completed"]

        completed = events[1]["data"]
        assert completed["job_id"] == wid
        assert completed["status"] == "completed"
        # ``record.result_summary`` was parsed from ``Task.result`` JSON.
        assert completed["result_summary"] == "task finished"
        assert completed["error_message"] is None
        # Tasks have no queue concept; the unified view-model collapses it.
        assert completed["queue_id"] is None

    async def test_failed_task_maps_error_message_correctly(
        self,
        engine: Engine,
        app_resolver_on: FastAPI,
    ):
        """``WorkRecord.error`` must surface as ``error_message`` in the
        ``completed`` payload (the frontend's existing field name)."""
        wid = _seed_completed_task(
            engine,
            result=None,
            error="boom: simulated failure",
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_resolver_on),
            base_url="http://test",
            timeout=httpx.Timeout(10.0),
        ) as async_client:
            async with async_client.stream(
                "GET", f"/api/jobs/{wid}/events"
            ) as response:
                assert response.status_code == 200
                events = await _read_sse_events(response, max_events=2)
                await response.aclose()

        completed = events[1]["data"]
        assert completed["status"] == "completed"
        assert completed["error_message"] == "boom: simulated failure"

    def test_unknown_work_id_returns_404(
        self,
        client_resolver_on: TestClient,
    ):
        response = client_resolver_on.get("/api/jobs/no-such-id/events")
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "Job not found"

    async def test_sse_content_type_header(
        self,
        engine: Engine,
        app_resolver_on: FastAPI,
    ):
        jid = _seed_completed_job(engine)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_resolver_on),
            base_url="http://test",
            timeout=httpx.Timeout(10.0),
        ) as async_client:
            async with async_client.stream(
                "GET", f"/api/jobs/{jid}/events"
            ) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")
                await response.aclose()

    async def test_resolver_emits_mission_fields_always_on(
        self,
        engine: Engine,
        app_resolver_on: FastAPI,
    ):
        """Populated variant — the SSE payload includes the three M1
        additive mission fields, always-on (WS3 removed the
        kill-switch).

        Companion to
        ``test_completed_job_via_resolver_emits_terminal_events`` —
        that test pins the unlinked-instance shape (keys present,
        values ``None``); this one pins the populated shape (values
        resolved from the linked ``Instance`` row via
        :class:`MissionResolver`).
        """
        from daemon.repositories.instance.models import InstanceStatus

        # Seed a backing Instance row in a TERMINAL state so the
        # liveness branch of MissionResolver._project yields
        # ``completed`` for ``mission_terminal_reason``; an
        # admission_state='dead' link would override to 'dead_letter'
        # (W4).
        iid = _seed_instance(
            engine, instance_id="inst-mission-on", status=InstanceStatus.COMPLETED.value
        )
        jid = _seed_completed_job(engine, instance_id=iid)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_resolver_on),
            base_url="http://test",
            timeout=httpx.Timeout(10.0),
        ) as async_client:
            async with async_client.stream(
                "GET", f"/api/jobs/{jid}/events"
            ) as response:
                assert response.status_code == 200
                events = await _read_sse_events(response, max_events=2)
                await response.aclose()

        completed = events[1]["data"]
        # Fix C fields are present (they're always emitted on the
        # unified path), as are the M1 additive keys (always-on).
        assert completed["job_type"] == "task"
        # The three M1 additive mission keys ARE present, with
        # ``mission_id`` equal to the backing ``instance_id`` per
        # the M1 spec §3 identity contract.
        assert "mission_id" in completed
        assert completed["mission_id"] == iid
        assert "mission_epoch" in completed
        assert completed["mission_epoch"] is not None
        assert "mission_terminal_reason" in completed
        # The terminal ``completed`` Instance status maps onto the
        # ``completed`` mission terminal_reason via the canonical
        # liveness mapping in MissionResolver._project.
        assert completed["mission_terminal_reason"] == "completed"


class TestResolverFlagDefault:
    """The flag read helper recognises explicit ``use_virtual_job_resolver=False``.

    Phase 1 (Job as Queue Proxy): the legacy JobItem-direct branch
    in ``jobs_streaming._resolve`` has been removed. Both the ON and
    OFF flag paths now route through ``service.get_work`` →
    ``WorkResolverService.resolve_work`` and project the result via
    ``_ResolvedWork.from_work_record`` (which collapses ``queue_id``
    to ``None``). The flag is preserved for compatibility with the
    Phase 7 cleanup, but it no longer changes the data path.
    """

    async def test_resolver_flag_off_still_routes_through_resolver(
        self,
        engine: Engine,
        service_with_resolver: JobQueueService,
    ):
        """``use_virtual_job_resolver=False`` → endpoint still uses the
        resolver and ``queue_id`` collapses to ``None``.

        Phase 1 collapsed the legacy ``_ResolvedWork.from_job`` branch
        onto the resolver path, so even with the flag OFF the
        endpoint reads through ``WorkResolverService.resolve_work``.
        The wire-format contract (``queue_id=None`` on the unified
        path) holds regardless of the flag — this is the post-Phase-1
        behaviour the test pins down.
        """
        app = FastAPI()
        app.include_router(streaming_router, prefix="/api")
        from daemon.routers.jobs_streaming import get_job_queue_service

        app.dependency_overrides[get_job_queue_service] = (
            lambda: service_with_resolver
        )
        # Explicit OFF — the legacy branch no longer exists in Phase 1,
        # so the OFF path still routes through the resolver. The flag
        # remains in the config schema for Phase 7 cleanup but it no
        # longer changes the data path.
        app.state.manager = SimpleNamespace(
            config=SimpleNamespace(
                job_system=SimpleNamespace(use_virtual_job_resolver=False),
            ),
        )

        jid = _seed_completed_job(engine, queue_id="queue-q")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            timeout=httpx.Timeout(10.0),
        ) as async_client:
            async with async_client.stream(
                "GET", f"/api/jobs/{jid}/events"
            ) as response:
                assert response.status_code == 200
                events = await _read_sse_events(response, max_events=2)
                await response.aclose()

        completed = events[1]["data"]
        # Phase 1 (Job as Queue Proxy): ``queue_id`` collapses to ``None``
        # on the unified resolver path even when the flag is OFF. The
        # WorkRecord view-model does not surface the JobItem-only
        # ``queue_id`` column.
        assert completed["queue_id"] is None
