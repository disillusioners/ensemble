"""Unit tests for the GET /api/work endpoint.

Phase 4 (2026-06-27) of ``feature/virtual-job-management-surface``.
Validates:

* ``kind`` filter splits into ``job`` / ``turn`` / ``report`` (and
  the backward-compat ``task`` alias matches turn+report).
* All other filters (status, project_id, instance_id) compose with
  the kind filter.
* 503 returned when WorkResolverService has not been wired in.
* 400 returned for unknown ``kind`` values.
* JSON serialization shape (ISO-8601 created_at, null fields).

The tests build a real in-memory SQLite database (StaticPool + FK
on) and run the actual ``WorkResolverService`` SQL, mirroring
``tests/unit/routers/test_jobs_streaming_resolver.py``. This pins
the kind-discrimination contract that the frontend consumes — if
``Task.task_type`` is misread or the ``kind="task"`` alias is lost,
the ``/api/work?kind=job`` tests fail.
"""

from __future__ import annotations

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
from daemon.repositories.job_queue.models import JobItem, JobQueue, AdmissionState
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.routers.work import (
    get_work_resolver,
    router as work_router,
    set_work_resolver,
)
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
    """In-memory SQLite engine (StaticPool + FK on)."""
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


@pytest.fixture
def client(resolver: WorkResolverService) -> TestClient:
    """TestClient with the work_resolver wired in via dependency_overrides.

    Uses ``app.dependency_overrides`` (the FastAPI-recommended way)
    so the real ``get_work_resolver`` factory is bypassed in favour
    of the test's resolver. This is what ``daemon/api.py`` does at
    runtime via ``set_work_resolver``.
    """
    app = FastAPI()
    app.include_router(work_router, prefix="/api")
    app.dependency_overrides[get_work_resolver] = lambda: resolver
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_work_resolver_global():
    """Reset the module-level ``_work_resolver`` around each test.

    The work router holds a module-level singleton that
    ``daemon/api.py`` wires in at startup. Each test needs a clean
    slate so ``test_uninitialized_resolver_returns_503`` can observe
    the 503 path (the default state in CI without a real lifespan
    run).
    """
    import daemon.routers.work as work_module
    original = work_module._work_resolver
    work_module._work_resolver = None
    yield
    work_module._work_resolver = original


# ─── Seed helpers ────────────────────────────────────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    agent_id: str = "developer",
    project_id: str | None = "test-project",
    parent_id: str | None = None,
) -> str:
    """Insert an Instance row and return its ID.

    ``parent_id`` defaults to ``None`` (a root instance); the
    P-A ``root_only`` tests pass a non-null value here to seed a
    child instance and assert that the GET /work endpoint omits
    work bound to it by default.
    """
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
            parent_id=parent_id,
        )
        s.add(inst)
        s.commit()
    return iid


def _seed_job(
    engine: Engine,
    *,
    job_id: str | None = None,
    instance_id: str | None = None,
    project_id: str | None = "test-project",
    status: str = AdmissionState.QUEUED.value,
    queue_id: str | None = None,
) -> str:
    """Insert a JobItem row and return its job_id."""
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
            project_id=project_id,
            priority=5,

            admission_state=status_to_admission(status),
            instance_id=instance_id,
            queue_id=queue_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            job_metadata={},
        )
        s.add(job)
        s.commit()
    return jid


def _seed_task(
    engine: Engine,
    *,
    work_id: str | None = None,
    instance_id: str | None = None,
    task_type: str = "process_message",
    status: str = TaskStatus.PENDING.value,
    result: str | None = None,
    error: str | None = None,
) -> str:
    """Insert a Task row and return its work_id."""
    wid = work_id or str(uuid.uuid4())
    if instance_id is None:
        instance_id = _seed_instance(engine)
    with Session(engine) as s:
        task = Task(
            work_id=wid,
            task_type=task_type,
            instance_id=instance_id,
            message_id=None,
            status=status,
            result=result,
            error=error,
            created_at=datetime.now(timezone.utc),
        )
        s.add(task)
        s.commit()
    return wid


# ─── Tests ──────────────────────────────────────────────────────────────────


class TestListWorkBasic:
    """Top-level coverage: GET /work with no filters and no data."""

    def test_list_work_returns_jobs_and_tasks(self, client: TestClient, engine: Engine):
        """Seed a Job + a report Task — GET /work returns both.

        Phase 4 partial collapse (2026-07-06): ``process_message``
        Tasks no longer appear in ``list_work`` — message turns flow
        through ``job_create`` and surface as JobItems. The Task side
        is report-only. The test pins the post-collapse union of one
        JobItem + one report Task.
        """
        job_id = _seed_job(engine)
        report_id = _seed_task(engine, task_type="process_report")

        resp = client.get("/api/work")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body) == 2
        kinds = {item["work_id"]: item["kind"] for item in body}
        assert kinds[job_id] == "job"
        assert kinds[report_id] == "report"

    def test_empty_result_returns_empty_array(self, client: TestClient):
        """No seeded data → empty list (not 404, not null)."""
        resp = client.get("/api/work")
        assert resp.status_code == 200
        assert resp.json() == []


class TestKindFilter:
    """Phase 4 contract: kind split into job / turn / report / task."""

    def test_kind_filter_job_returns_only_jobs(self, client: TestClient, engine: Engine):
        job_id = _seed_job(engine)
        _seed_task(engine, task_type="process_message")
        _seed_task(engine, task_type="process_report")

        resp = client.get("/api/work?kind=job")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body) == 1
        assert body[0]["work_id"] == job_id
        assert body[0]["kind"] == "job"

    def test_kind_filter_turn_returns_400(self, client: TestClient, engine: Engine):
        """Phase 4 partial collapse: ``kind=turn`` is rejected with HTTP 400.

        The ``"turn"`` kind was the pre-collapse Task-side kind for
        ``process_message`` Tasks. Post-collapse, message turns flow
        through ``job_create`` and surface as JobItems
        (``kind="job"``); the router rejects ``kind="turn"`` so a
        typo doesn't silently return an empty list.
        """
        _seed_job(engine)
        _seed_task(engine, task_type="process_message")
        _seed_task(engine, task_type="process_report")

        resp = client.get("/api/work?kind=turn")
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "turn" in detail["error"]
        assert set(detail["accepted"]) == {"job", "report"}

    def test_kind_filter_report_returns_only_reports(self, client: TestClient, engine: Engine):
        _seed_job(engine)
        _seed_task(engine, task_type="process_message")
        report_id = _seed_task(engine, task_type="process_report")

        resp = client.get("/api/work?kind=report")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["work_id"] == report_id
        assert body[0]["kind"] == "report"

    def test_kind_filter_task_returns_400(self, client: TestClient, engine: Engine):
        """Phase 4 partial collapse: ``kind=task`` is rejected with HTTP 400.

        The ``"task"`` kind was a backward-compat alias for the union
        of turn + report before collapse. Post-collapse, turns are
        JobItems and the only remaining Task-side kind is ``report``,
        so the alias no longer maps to any record. The router rejects
        the value to fail loudly on legacy callers.
        """
        _seed_job(engine)
        _seed_task(engine, task_type="process_message")
        _seed_task(engine, task_type="process_report")

        resp = client.get("/api/work?kind=task")
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "task" in detail["error"]
        assert set(detail["accepted"]) == {"job", "report"}

    def test_kind_filter_report_includes_send_report_type(self, client: TestClient, engine: Engine):
        """``send_report`` TaskType should also map to kind='report'."""
        _seed_task(engine, task_type="send_report")
        resp = client.get("/api/work?kind=report")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["kind"] == "report"

    def test_kind_filter_no_match_returns_empty(self, client: TestClient, engine: Engine):
        """``?kind=report`` with no report seeded → empty list.

        Phase 4 partial collapse: the test exercises the
        kind-filter narrowing with a valid (``"report"``) kind and
        no report-seed — the resolver returns the JobItem only when
        ``kind="job"`` is requested, but ``kind="report"`` with no
        report-seed returns ``[]``. (The pre-collapse equivalent
        used ``?kind=turn`` which now returns 400.)
        """
        _seed_job(engine)
        # No report seeded — ``kind=report`` returns empty.

        resp = client.get("/api/work?kind=report")
        assert resp.status_code == 200
        assert resp.json() == []


    def test_kind_filter_job_with_only_jobs_returns_jobs(self, client: TestClient, engine: Engine):
        """``kind=job`` with a job seeded + a report Task → returns only the job.

        Companion to ``test_kind_filter_no_match_returns_empty`` —
        exercises the JobItem branch of the kind filter.
        """
        job_id = _seed_job(engine)
        _seed_task(engine, task_type="process_report")

        resp = client.get("/api/work?kind=job")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["work_id"] == job_id


class TestStatusFilter:
    """Canonical status filter — Task ``running`` maps to ``processing``."""

    def test_status_filter_processing_returns_running_task(self, client: TestClient, engine: Engine):
        """``?status=processing`` returns the report Task in RUNNING.

        Phase 4 partial collapse: only report Tasks surface in
        ``list_work`` (``process_message`` Tasks are JobItems now).
        """
        _seed_task(engine, task_type="process_report", status=TaskStatus.PENDING.value)
        running_id = _seed_task(engine, task_type="process_report", status=TaskStatus.RUNNING.value)
        _seed_task(engine, task_type="process_report", status=TaskStatus.COMPLETED.value)

        resp = client.get("/api/work?status=processing")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["work_id"] == running_id
        assert body[0]["status"] == "processing"

    def test_status_filter_comma_separated_returns_union(
        self, client: TestClient, engine: Engine
    ):
        """Comma-separated status filter returns the union (Phase 4 fix).

        Regression test: pre-fix, ``?status=pending,processing`` looked up
        the literal string ``"pending,processing"`` in the reverse
        canonical map, found nothing, and silently returned ``[]``. After
        the fix, the resolver splits on ``,`` and unions the per-token
        source-status sets, so a pending task AND a running task (which
        canonicalises to ``processing``) both come back.

        Phase 4 partial collapse: only report Tasks surface in
        ``list_work``.
        """
        pending_id = _seed_task(engine, task_type="process_report", status=TaskStatus.PENDING.value)
        running_id = _seed_task(engine, task_type="process_report", status=TaskStatus.RUNNING.value)
        _seed_task(engine, task_type="process_report", status=TaskStatus.COMPLETED.value)

        resp = client.get("/api/work?status=pending,processing")
        assert resp.status_code == 200
        body = resp.json()
        returned_ids = {item["work_id"] for item in body}
        assert returned_ids == {pending_id, running_id}
        # Both canonical statuses are visible on the wire.
        statuses = {item["status"] for item in body}
        assert statuses == {"pending", "processing"}

    def test_status_filter_comma_separated_handles_whitespace_and_dupes(
        self, client: TestClient, engine: Engine
    ):
        """Whitespace stripping and deduplication on the comma list.

        Phase 4 partial collapse: uses a report Task.
        """
        running_id = _seed_task(engine, task_type="process_report", status=TaskStatus.RUNNING.value)

        # Note the spaces, the mixed case, and the duplicate token.
        resp = client.get("/api/work?status=%20processing%20,PROCESSING,running")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["work_id"] == running_id


class TestInstanceFilter:
    """instance_id filter narrows the result to one instance."""

    def test_instance_id_filter(self, client: TestClient, engine: Engine):
        """instance_id filter narrows the result to one instance.

        Phase 4 partial collapse: only report Tasks surface in
        ``list_work``, so the seeded Tasks use ``process_report``.
        """
        inst_a = _seed_instance(engine, instance_id="inst-a")
        inst_b = _seed_instance(engine, instance_id="inst-b")
        task_a_id = _seed_task(engine, instance_id=inst_a, task_type="process_report")
        _seed_task(engine, instance_id=inst_b, task_type="process_report")

        resp = client.get(f"/api/work?instance_id={inst_a}")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["work_id"] == task_a_id


class TestProjectFilter:
    """project_id filter narrows the result to one project."""

    def test_project_id_filter_on_jobs(self, client: TestClient, engine: Engine):
        job_p1 = _seed_job(engine, project_id="proj-1")
        _seed_job(engine, project_id="proj-2")

        resp = client.get("/api/work?project_id=proj-1&kind=job")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["work_id"] == job_p1


class TestCombinedFilters:
    """kind and status compose with AND."""

    def test_kind_and_status_compose(self, client: TestClient, engine: Engine):
        """kind and status compose with AND.

        Phase 4 partial collapse: only ``kind="report"`` is valid
        post-collapse; ``kind="turn"`` / ``kind="task"`` return HTTP
        400. The test seeds a running report Task + a pending
        report Task + a running report (with deferred-flag-cleanup)
        and filters by ``kind=report&status=processing`` — only the
        running one surfaces.
        """
        running_report = _seed_task(
            engine,
            task_type="process_report",
            status=TaskStatus.RUNNING.value,
        )
        _seed_task(
            engine,
            task_type="process_report",
            status=TaskStatus.PENDING.value,
        )

        resp = client.get("/api/work?kind=report&status=processing")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["work_id"] == running_report


class TestErrorResponses:
    """HTTP error paths: 400 for bad kind, 503 for uninitialized resolver."""

    def test_invalid_kind_returns_400(self, client: TestClient):
        resp = client.get("/api/work?kind=invalid")
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "invalid" in detail["error"].lower()
        # Phase 4 partial collapse: ``kind=turn`` and ``kind=task``
        # are now rejections of their own — the only accepted kinds
        # are ``"job"`` and ``"report"``.
        assert set(detail["accepted"]) == {"job", "report"}

    def test_uninitialized_resolver_returns_503(self):
        """Without ``set_work_resolver``, GET /work should 503."""
        app = FastAPI()
        app.include_router(work_router, prefix="/api")
        # NO dependency_overrides — exercises the None branch in get_work_resolver.
        # The autouse ``_reset_work_resolver_global`` fixture has already
        # cleared the module-level global for us.
        test_client = TestClient(app)
        resp = test_client.get("/api/work")
        assert resp.status_code == 503
        assert "not initialized" in str(resp.json()).lower()


class TestSerialization:
    """JSON serialization shape — the frontend contract."""

    def test_created_at_serialized_as_iso8601(self, client: TestClient, engine: Engine):
        """Phase 4 partial collapse: only report Tasks surface in
        ``list_work``, so the test seeds a ``process_report`` Task."""
        wid = _seed_task(engine, task_type="process_report")
        resp = client.get("/api/work")
        assert resp.status_code == 200
        body = resp.json()
        item = next(i for i in body if i["work_id"] == wid)
        # Should be a string in ISO 8601 format
        assert isinstance(item["created_at"], str)
        # Round-trip parse should succeed
        parsed = datetime.fromisoformat(item["created_at"])
        assert isinstance(parsed, datetime)

    def test_response_field_shape(self, client: TestClient, engine: Engine):
        """All advertised fields present, none extra, none missing.

        Phase 1 (Job as Queue Proxy): ``started_at`` and ``completed_at``
        were added to the :class:`WorkRecord` to surface Instance
        execution timing through the resolver. They appear on the wire
        as ``None`` for Task-backed rows (the timing data lives on the
        Instance, which the Task-side view-model doesn't model in
        Phase 1).

        Phase 1 (F1, defer-seam bugfix): ``message_id`` was added to
        the :class:`WorkRecord` and surfaces on the wire as the
        cross-system correlation key. ``None`` for legacy rows that
        pre-date the F1 fix; UUID strings for rows stamped at
        enqueue time.

        Phase 4 partial collapse: only report Tasks surface in
        ``list_work``. The Task is seeded as ``process_report`` so
        the assertion sees ``kind="report"`` (the post-collapse
        Task-side kind).
        """
        # Result is a JSON object — WorkResolverService's
        # ``_parse_task_result_summary`` re-serialises non-string JSON
        # values via ``json.dumps``, so the result_summary is the JSON
        # string ``'{"output": "hello"}'`` rather than the decoded
        # dict (the API contract is JSON-safe strings).
        wid = _seed_task(
            engine,
            task_type="process_report",
            result='{"output": "hello"}',
        )
        resp = client.get("/api/work")
        body = resp.json()
        item = next(i for i in body if i["work_id"] == wid)
        assert set(item.keys()) == {
            "work_id", "kind", "status", "instance_id",
            "project_id", "agent_id", "result_summary", "error",
            "created_at",
            # Phase 1 (Job as Queue Proxy): execution-timing fields
            # sourced from the joined Instance. ``None`` on Task rows.
            "started_at",
            "completed_at",
            # Phase 1 F1 (defer-seam bugfix): cross-system correlation
            # key. ``None`` on legacy rows that pre-date the F1 fix;
            # UUID strings on rows stamped at enqueue time.
            "message_id",
            # Fix C (read-model split, additive). Task-backed rows
            # carry ``None`` for both — the mission/mirror concept
            # does not apply to reports. Consumers can branch on
            # ``kind`` and ignore these keys for report rows.
            "job_type",
            "mission_liveness",
            # M1 (mission-class, 2026-09-02) — additive mission
            # projection fields (always-on since WS3; the
            # ``ENSEMBLE_MISSION_PROJECTION_ENABLED`` kill-switch
            # was removed). The four keys surface verbatim on every
            # record; ``None`` on Task-backed records (the work is
            # its own mission here).
            "mission_id",
            "mission_epoch",
            "mission_terminal_reason",
            # M2 (mission-class, 2026-09-02) — anti-trap guardrails.
            # ``outcome`` stays ``None`` on the work (transport)
            # surface (contract draft §3.2); ``mission_ref`` is the
            # cross-reference payload tying the work row to its
            # linked mission (§3.3, mandatory on terminal
            # payloads).
            "outcome",
            "mission_ref",
        }
        # Phase 4 partial collapse: report Task surfaces as
        # ``kind="report"`` (the legacy ``"turn"`` is gone — turns
        # are JobItems now).
        assert item["kind"] == "report"
        assert item["result_summary"] == '{"output": "hello"}'
        # Timing fields are present on the wire (Phase 1) but ``None``
        # for Task-backed rows because the Task-side WorkRecord does
        # not source Instance timing.
        assert item["started_at"] is None
        assert item["completed_at"] is None

    def test_none_fields_serialized_as_null(self, client: TestClient, engine: Engine):
        """None values round-trip as JSON null (not omitted).

        Phase 4 partial collapse: seeds a report Task (only report
        Tasks surface in ``list_work``).
        """
        wid = _seed_task(engine, task_type="process_report")
        resp = client.get("/api/work")
        body = resp.json()
        item = next(i for i in body if i["work_id"] == wid)
        # JSON null is ``None`` in Python after deserialisation.
        # We explicitly check the raw JSON to assert the wire shape.
        raw = resp.text
        assert '"result_summary":null' in raw or '"result_summary": null' in raw
        assert '"error":null' in raw or '"error": null' in raw
        # Belt-and-suspenders: decoded nulls are Python None.
        assert item["result_summary"] is None
        assert item["error"] is None


# ─── P-A: GET /api/work root_only query param ───────────────────────────────
# Phase 5 (2026-06-27) of ``feature/virtual-job-management-surface``.
# The router forwards the ``root_only`` query parameter to
# ``WorkResolverService.list_work``. Default is ``true`` (omits
# child-instance work); ``?root_only=false`` returns the full union.


class TestRootOnlyParam:
    """``GET /api/work?root_only=...`` controls P-A child-instance scoping."""

    def test_work_endpoint_root_only_param(
        self, client: TestClient, engine: Engine
    ):
        """Default omits child-instance rows; ``?root_only=false``
        includes them.

        Mirrors the resolver-level test in
        ``tests/unit/services/test_work_resolver.py`` at the HTTP
        boundary — proves the new query param is plumbed through
        end-to-end and that the management view defaults to the
        root-scoped subset the jober cares about.

        Phase 4 partial collapse: only report Tasks surface in
        ``list_work``. The seeded Tasks use ``process_report``.
        """
        root_id = _seed_instance(engine, instance_id="inst-router-root")
        child_id = _seed_instance(
            engine,
            instance_id="inst-router-child",
            parent_id="inst-router-root",
        )
        root_wid = _seed_task(engine, instance_id=root_id, task_type="process_report")
        child_wid = _seed_task(engine, instance_id=child_id, task_type="process_report")

        # Default (root_only=True) → only the root task is returned.
        resp = client.get("/api/work")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert {item["work_id"] for item in body} == {root_wid}

        # Explicit ?root_only=false → both come back.
        resp = client.get("/api/work?root_only=false")
        assert resp.status_code == 200
        body = resp.json()
        assert {item["work_id"] for item in body} == {root_wid, child_wid}

        # Explicit ?root_only=true matches the default.
        resp = client.get("/api/work?root_only=true")
        assert resp.status_code == 200
        body = resp.json()
        assert {item["work_id"] for item in body} == {root_wid}