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

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import JobItem, JobQueue, JobStatus
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.routers.jobs_streaming import router as streaming_router
from daemon.services.job_queue_service import JobQueueService
from daemon.services.work_resolver import WorkResolverService


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
) -> JobQueueService:
    """Legacy JobQueueService — no resolver wired (force ``get_job`` branch)."""
    return JobQueueService(
        repository=job_repo,
        lock_manager=MagicMock(),
        queue_repo=MagicMock(),
        instance_manager=MagicMock(),
    )


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
            status="running",
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
) -> str:
    """Insert a JobItem already in the terminal ``completed`` state.

    If ``queue_id`` is set, also inserts a matching ``JobQueue`` row so
    the FK constraint on ``job_queue_items.queue_id`` is satisfied.
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
            status=JobStatus.COMPLETED.value,
            result_summary="done",
            error_message=None,
            instance_id=None,
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


def _read_sse_events(response, *, max_events: int = 4) -> list[dict]:
    """Parse SSE ``event:`` / ``data:`` pairs into a list of dicts."""
    events: list[dict] = []
    current: dict = {}
    lines_read = 0
    for line in response.iter_lines():
        lines_read += 1
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
    """Legacy branch — polls ``JobItem`` directly via ``service.get_job``.

    The endpoint must keep emitting the exact JSON shape it did before the
    Batch 4b rewire so the frontend does not need a coordinated change.
    """

    def test_completed_job_emits_terminal_events_with_legacy_fields(
        self,
        engine: Engine,
        client_resolver_off: TestClient,
        job_repo: JobRepository,
    ):
        """A completed JobItem must stream ``connected`` + ``completed`` with
        ``result_summary`` / ``error_message`` / ``queue_id`` populated."""
        jid = _seed_completed_job(engine, queue_id="queue-xyz")

        with client_resolver_off.stream(
            "GET", f"/api/jobs/{jid}/events"
        ) as response:
            assert response.status_code == 200
            events = _read_sse_events(response, max_events=2)

        names = [e["event"] for e in events]
        assert names == ["connected", "completed"]

        connected = events[0]["data"]
        assert connected["job_id"] == jid
        assert connected["status"] == "completed"
        assert connected["queue_id"] == "queue-xyz"

        completed = events[1]["data"]
        assert completed["job_id"] == jid
        assert completed["status"] == "completed"
        assert completed["result_summary"] == "done"
        assert completed["error_message"] is None
        assert completed["queue_id"] == "queue-xyz"

    def test_unknown_work_id_returns_404(
        self,
        client_resolver_off: TestClient,
    ):
        response = client_resolver_off.get("/api/jobs/no-such-id/events")
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "Job not found"

    def test_sse_content_type_header(
        self,
        engine: Engine,
        client_resolver_off: TestClient,
    ):
        jid = _seed_completed_job(engine)
        with client_resolver_off.stream(
            "GET", f"/api/jobs/{jid}/events"
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")


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

    def test_completed_job_via_resolver_emits_terminal_events(
        self,
        engine: Engine,
        client_resolver_on: TestClient,
    ):
        """JobItem-backed work_id resolves through the resolver."""
        jid = _seed_completed_job(engine, queue_id="queue-original")

        with client_resolver_on.stream(
            "GET", f"/api/jobs/{jid}/events"
        ) as response:
            assert response.status_code == 200
            events = _read_sse_events(response, max_events=2)

        names = [e["event"] for e in events]
        assert names == ["connected", "completed"]

        completed = events[1]["data"]
        # Wire-format contract: same keys as the legacy branch.
        assert set(completed.keys()) == {
            "job_id",
            "status",
            "result_summary",
            "error_message",
            "queue_id",
        }
        assert completed["job_id"] == jid
        assert completed["status"] == "completed"
        assert completed["result_summary"] == "done"
        # ``WorkRecord`` does not surface JobItem.queue_id, so the SSE
        # payload collapses it to ``None`` instead of leaking an empty
        # string. The frontend accepts either null or a real value.
        assert completed["queue_id"] is None

    def test_completed_task_via_resolver_emits_terminal_events(
        self,
        engine: Engine,
        client_resolver_on: TestClient,
    ):
        """Task-backed work_id resolves through the resolver too.

        This is the headline Batch 4b behaviour: a Task row never
        surfaced through the SSE endpoint before, but the rewire means
        the same ``/api/jobs/{work_id}/events`` URL now streams Task
        lifecycle events to any caller that knows the work_id.
        """
        wid = _seed_completed_task(engine, result="task finished", error=None)

        with client_resolver_on.stream(
            "GET", f"/api/jobs/{wid}/events"
        ) as response:
            assert response.status_code == 200
            events = _read_sse_events(response, max_events=2)

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

    def test_failed_task_maps_error_message_correctly(
        self,
        engine: Engine,
        client_resolver_on: TestClient,
    ):
        """``WorkRecord.error`` must surface as ``error_message`` in the
        ``completed`` payload (the frontend's existing field name)."""
        wid = _seed_completed_task(
            engine,
            result=None,
            error="boom: simulated failure",
        )

        with client_resolver_on.stream(
            "GET", f"/api/jobs/{wid}/events"
        ) as response:
            assert response.status_code == 200
            events = _read_sse_events(response, max_events=2)

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

    def test_sse_content_type_header(
        self,
        engine: Engine,
        client_resolver_on: TestClient,
    ):
        jid = _seed_completed_job(engine)
        with client_resolver_on.stream(
            "GET", f"/api/jobs/{jid}/events"
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")


class TestResolverFlagDefault:
    """The flag read helper recognises explicit ``use_virtual_job_resolver=False``.

    Note: the Phase 3 ``tests/conftest.py::_ensure_app_state_manager``
    autouse fixture auto-creates a ``MagicMock`` ``app.state.manager``
    when missing, so the "no manager at all" branch in
    :func:`daemon.routers.jobs_streaming._use_resolver` is suppressed in
    the test suite. This class instead exercises the rollback path by
    explicitly setting the flag to ``False`` — the production scenario
    where operators turn off the resolver to bypass the merged read API
    after a regression.
    """

    def test_resolver_flag_off_routes_through_jobitem_branch(
        self,
        engine: Engine,
        service_with_resolver: JobQueueService,
    ):
        """``use_virtual_job_resolver=False`` → endpoint uses ``get_job`` and
        the ``queue_id`` round-trips verbatim from the JobItem row."""
        app = FastAPI()
        app.include_router(streaming_router, prefix="/api")
        from daemon.routers.jobs_streaming import get_job_queue_service

        app.dependency_overrides[get_job_queue_service] = (
            lambda: service_with_resolver
        )
        # Explicit OFF — should take the legacy JobItem branch even though
        # the service has the resolver wired (the flag is authoritative).
        from types import SimpleNamespace
        app.state.manager = SimpleNamespace(
            config=SimpleNamespace(
                job_system=SimpleNamespace(use_virtual_job_resolver=False),
            ),
        )

        jid = _seed_completed_job(engine, queue_id="queue-q")
        client = TestClient(app)
        with client.stream("GET", f"/api/jobs/{jid}/events") as response:
            assert response.status_code == 200
            events = _read_sse_events(response, max_events=2)

        completed = events[1]["data"]
        # Legacy branch preserves queue_id verbatim from JobItem.
        assert completed["queue_id"] == "queue-q"