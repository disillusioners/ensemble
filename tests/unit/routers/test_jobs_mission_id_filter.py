"""Tests for the ``mission_id`` filter on ``GET /api/jobs``.

Mission tree panel (``feature/job-queue-mission-tree``, 2026-09-07):
the jobs list endpoint gains a ``mission_id`` query param that narrows
``job_queue_items`` by ``JobItem.instance_id`` (the mission identity
rule: ``mission_id == instance_id``). The filter lives at three layers
(mirroring the ``job_types`` filter's structure in
``tests/unit/tools/test_job_list_job_types_filter.py``):

  1. ``daemon/repositories/job_queue/repository.py::JobRepository.list``
     — adds ``instance_id=`` predicate to the existing count + page
     queries (no extra SELECT).
  2. ``daemon/services/job_queue_service.py::JobQueueService.list_jobs``
     — passes ``instance_id`` through to the repository.
  3. ``daemon/routers/jobs_crud.py::list_jobs`` — accepts the
     ``mission_id`` query param (primary) plus an ``instance_id``
     deprecated alias for the same filter.

Each test pins one layer's contract. The wire-level test mounts the
real jobs router over a real ``JobQueueService`` over a real SQLite
engine (the conventions recipe — file-backed, ``NullPool``, WAL,
``busy_timeout``); the resolver is intentionally unwired so the
enrichment leg degrades to the documented JobItem-mirror path (no
behavioural surprise on the ``mission_id`` filter — the page is
defined by the JobItem SQL only).

All tests are READ-ONLY: zero DML on the jobs table — the filter only
narrows what the existing query returns.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401

from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.routers.jobs_crud import (
    get_dead_letter_svc,
    get_job_queue_service,
    router as jobs_crud_router,
)
from daemon.services.dead_letter_service import DeadLetterService
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import JobQueueService


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (the conventions recipe)."""
    db_path = tmp_path / "jobs-mission-filter.sqlite"
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
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def queue_repo(engine: Engine) -> JobQueueRepository:
    return JobQueueRepository(engine)


@pytest.fixture
def lock_repo(engine: Engine) -> LockRepository:
    return LockRepository(engine)


@pytest.fixture
def lock_manager(lock_repo: LockRepository) -> JobLockManager:
    """Real JobLockManager (read-only path — no locks acquired)."""
    return JobLockManager(lock_repo=lock_repo)


@pytest.fixture
def job_queue_service(
    job_repo: JobRepository,
    lock_manager: JobLockManager,
    queue_repo: JobQueueRepository,
) -> JobQueueService:
    """Real ``JobQueueService`` over the test engine. The
    ``_work_resolver`` is intentionally unwired — the ``list_jobs``
    router leg that calls ``service.list_work`` degrades to the
    documented JobItem-mirror path (an empty WorkRecord map), which
    is the established shape for the partial-wiring case."""
    return JobQueueService(job_repo, lock_manager, queue_repo)


@pytest.fixture
def dead_letter_service(job_repo: JobRepository, engine: Engine):
    """Real ``DeadLetterService`` over the test engine."""
    from daemon.repositories.job_queue.dead_letter_repository import (
        DeadLetterRepository,
    )

    return DeadLetterService(job_repo, DeadLetterRepository(engine))


@pytest.fixture
def client(job_queue_service: JobQueueService, dead_letter_service):
    """TestClient with the jobs router wired to real services.

    Resets the module-level DI singletons after the test so other
    suites see ``503`` (the documented "service not initialized"
    behaviour) and the singleton does not leak across tests.
    """
    get_job_queue_service.set_service(job_queue_service)
    get_dead_letter_svc.set_service(dead_letter_service)
    app = FastAPI()
    app.include_router(jobs_crud_router, prefix="/api")
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        get_job_queue_service.set_service(None)
        get_dead_letter_svc.set_service(None)


# ─── Seed helpers ───────────────────────────────────────────────────────────


def _seed_job(
    engine: Engine,
    *,
    job_id: str | None = None,
    instance_id: str | None = None,
    admission_state: str = AdmissionState.QUEUED.value,
    job_type: str = "task",
    terminal_reason: str | None = None,
) -> str:
    """Insert one ``JobItem`` row; return its id."""
    jid = job_id or f"job-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        job = JobItem(
            job_id=jid,
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="mission-tree test",
            source="api",
            project_id="test-project",
            priority=5,
            admission_state=admission_state,
            terminal_reason=terminal_reason,
            instance_id=instance_id,
            created_at=now,
            updated_at=now,
            job_metadata={},
            job_type=job_type,
        )
        s.add(job)
        s.commit()
    return jid


# ─── Repository layer: SQL-level ``instance_id`` predicate ────────────────


class TestJobRepositoryInstanceIdFilter:
    """``JobRepository.list`` honours the ``instance_id`` parameter."""

    def test_no_param_returns_all_jobs(
        self, job_repo: JobRepository, engine: Engine
    ) -> None:
        """Back-compat: omitting ``instance_id`` returns every row."""
        _seed_job(engine, instance_id="m-1")
        _seed_job(engine, instance_id="m-2")
        _seed_job(engine, instance_id=None)  # unlinked
        jobs, total = job_repo.list()
        assert total == 3
        assert {j.instance_id for j in jobs} == {"m-1", "m-2", None}

    def test_filter_returns_only_missions_jobs(
        self, job_repo: JobRepository, engine: Engine
    ) -> None:
        """``instance_id='m-1'`` returns only rows whose
        ``JobItem.instance_id == 'm-1'``."""
        _seed_job(engine, instance_id="m-1")
        _seed_job(engine, instance_id="m-1")
        _seed_job(engine, instance_id="m-2")
        _seed_job(engine, instance_id=None)
        jobs, total = job_repo.list(instance_id="m-1")
        assert total == 2
        assert {j.instance_id for j in jobs} == {"m-1"}
        assert len(jobs) == 2

    def test_unknown_mission_id_returns_empty(
        self, job_repo: JobRepository, engine: Engine
    ) -> None:
        """An ``instance_id`` with no matching jobs ⇒ empty page (the
        documented "unknown filter ⇒ empty" shape — no 404)."""
        _seed_job(engine, instance_id="m-1")
        jobs, total = job_repo.list(instance_id="m-does-not-exist")
        assert jobs == []
        assert total == 0

    def test_empty_string_filters_by_empty(
        self, job_repo: JobRepository, engine: Engine
    ) -> None:
        """W-1 (second-pass review fold, 2026-09-07): ``instance_id=''``
        is a REAL filter that matches nothing ⇒ empty page with
        ``total == 0`` — it is NOT silently dropped (which would
        return every row). Count and page queries agree."""
        _seed_job(engine, instance_id="m-1")
        _seed_job(engine, instance_id=None)
        jobs, total = job_repo.list(instance_id="")
        assert total == 0, (
            "empty-string filter must filter-by-empty in the COUNT "
            "query, not be dropped"
        )
        assert jobs == [], (
            "empty-string filter must filter-by-empty in the PAGE "
            "query, not be dropped"
        )

    def test_composes_with_statuses(
        self, job_repo: JobRepository, engine: Engine
    ) -> None:
        """``instance_id`` composes (AND) with the legacy ``statuses``
        filter — narrowing on both columns returns the intersection."""
        # m-1: one queued + one done (terminal_reason='completed' +
        # admission_state='done' so the legacy vocabulary maps).
        _seed_job(engine, instance_id="m-1", admission_state=AdmissionState.QUEUED.value)
        _seed_job(
            engine,
            instance_id="m-1",
            admission_state=AdmissionState.DONE.value,
            terminal_reason="completed",
        )
        # m-2: a done row that the mission filter must EXCLUDE.
        _seed_job(
            engine,
            instance_id="m-2",
            admission_state=AdmissionState.DONE.value,
            terminal_reason="completed",
        )
        jobs, total = job_repo.list(
            instance_id="m-1", statuses=["completed"]
        )
        assert total == 1
        assert jobs[0].instance_id == "m-1"
        assert jobs[0].admission_state == AdmissionState.DONE.value


# ─── Service layer: ``JobQueueService.list_jobs`` passes through ──────────


class TestJobQueueServiceInstanceIdFilter:
    """``JobQueueService.list_jobs(instance_id=...)`` forwards the
    filter to the repository (no DML, no per-row work)."""

    @pytest.mark.asyncio
    async def test_service_forwards_instance_id(
        self, job_queue_service: JobQueueService, engine: Engine
    ) -> None:
        _seed_job(engine, instance_id="m-1")
        _seed_job(engine, instance_id="m-1")
        _seed_job(engine, instance_id="m-2")
        jobs = await job_queue_service.list_jobs(instance_id="m-1")
        assert len(jobs) == 2
        assert {j.instance_id for j in jobs} == {"m-1"}

    @pytest.mark.asyncio
    async def test_service_no_param_returns_all(
        self, job_queue_service: JobQueueService, engine: Engine
    ) -> None:
        _seed_job(engine, instance_id="m-1")
        _seed_job(engine, instance_id="m-2")
        jobs = await job_queue_service.list_jobs()
        assert {j.instance_id for j in jobs} == {"m-1", "m-2"}

    @pytest.mark.asyncio
    async def test_service_empty_string_filters_by_empty(
        self, job_queue_service: JobQueueService, engine: Engine
    ) -> None:
        """W-1 (second-pass review fold, 2026-09-07): a direct
        (non-HTTP) caller passing ``instance_id=""`` gets an EMPTY
        result set — the empty string is a real filter, not dropped."""
        _seed_job(engine, instance_id="m-1")
        _seed_job(engine, instance_id=None)
        jobs = await job_queue_service.list_jobs(instance_id="")
        assert jobs == []


# ─── Router layer: ``GET /api/jobs?mission_id=...`` wire contract ─────────


class TestJobsRouterMissionIdFilter:
    """The HTTP surface accepts ``mission_id``, threads it through
    the service, and returns only that mission's jobs. Composes with
    the existing filters; the ``instance_id`` query param is a
    deprecated alias for the same filter."""

    def test_mission_id_returns_only_that_missions_jobs(
        self, client: TestClient, engine: Engine
    ) -> None:
        _seed_job(engine, instance_id="m-1")
        _seed_job(engine, instance_id="m-1")
        _seed_job(engine, instance_id="m-2")

        resp = client.get("/api/jobs", params={"mission_id": "m-1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["jobs"]) == 2
        assert {j["instance_id"] for j in body["jobs"]} == {"m-1"}

    def test_no_param_unchanged(
        self, client: TestClient, engine: Engine
    ) -> None:
        """No ``mission_id`` and no ``instance_id`` ⇒ every job is
        returned (back-compat with the pre-filter contract)."""
        _seed_job(engine, instance_id="m-1")
        _seed_job(engine, instance_id="m-2")
        _seed_job(engine, instance_id=None)

        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert {j["instance_id"] for j in body["jobs"]} == {"m-1", "m-2", None}

    def test_mission_id_composes_with_status_filter(
        self, client: TestClient, engine: Engine
    ) -> None:
        """``mission_id`` AND ``status`` compose — intersection only."""
        _seed_job(engine, instance_id="m-1", admission_state=AdmissionState.QUEUED.value)
        _seed_job(
            engine,
            instance_id="m-1",
            admission_state=AdmissionState.DONE.value,
            terminal_reason="completed",
        )
        _seed_job(
            engine,
            instance_id="m-2",
            admission_state=AdmissionState.DONE.value,
            terminal_reason="completed",
        )
        resp = client.get(
            "/api/jobs", params={"mission_id": "m-1", "status": "completed"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["jobs"][0]["instance_id"] == "m-1"
        assert body["jobs"][0]["status"] == "completed"

    def test_instance_id_alias_accepted(
        self, client: TestClient, engine: Engine
    ) -> None:
        """``?instance_id=...`` (the deprecated alias) returns the
        same set as ``?mission_id=...``."""
        _seed_job(engine, instance_id="m-1")
        _seed_job(engine, instance_id="m-2")

        resp = client.get("/api/jobs", params={"instance_id": "m-1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["jobs"][0]["instance_id"] == "m-1"

    def test_mission_id_primary_over_alias(
        self, client: TestClient, engine: Engine
    ) -> None:
        """When both ``mission_id`` and ``instance_id`` are supplied
        (valid values), ``mission_id`` wins — the deprecated alias is
        ignored."""
        _seed_job(engine, instance_id="m-1")
        _seed_job(engine, instance_id="m-2")

        resp = client.get(
            "/api/jobs",
            params={"mission_id": "m-1", "instance_id": "m-2"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["jobs"][0]["instance_id"] == "m-1"

    def test_empty_mission_id_rejected_422(
        self, client: TestClient, engine: Engine
    ) -> None:
        """②/S-1 param hardening (second-pass review fold,
        2026-09-07): ``mission_id`` carries ``min_length=1``, so an
        EMPTY primary value is rejected by FastAPI with 422 BEFORE
        the handler runs — the empty value never reaches the
        filter-resolution logic.

        SPEC-CONFLICT RESOLUTION: the originally requested wire pin
        for this case was "empty page", but that contract is
        unreachable once ``min_length=1`` lands (the 422 happens
        upstream). This test pins the ACTUAL contract: 422. The
        filter-by-empty semantics for an empty value are pinned at
        the wire only via the deprecated ``instance_id`` alias (see
        ``test_empty_instance_id_alias_filters_by_empty``) and at the
        repo/service layers for direct callers.
        """
        _seed_job(engine, instance_id="m-1")
        resp = client.get("/api/jobs", params={"mission_id": ""})
        assert resp.status_code == 422
        # The seed row is untouched — read-only endpoint.
        jobs, total = JobRepository(engine).list()
        assert total == 1

    def test_empty_instance_id_alias_filters_by_empty(
        self, client: TestClient, engine: Engine
    ) -> None:
        """W-1 at the wire, via the deprecated alias: the
        ``instance_id`` Query has NO ``min_length``, so an EMPTY
        alias value reaches the handler and — per the ``is not
        None`` resolution — filters-by-empty ⇒ 200 with an empty
        page (NOT every row, NOT a 422)."""
        _seed_job(engine, instance_id="m-1")
        _seed_job(engine, instance_id=None)
        resp = client.get("/api/jobs", params={"instance_id": ""})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["jobs"] == []

    def test_unknown_mission_id_empty_page(
        self, client: TestClient, engine: Engine
    ) -> None:
        """An unknown ``mission_id`` ⇒ 200 with an empty page (the
        documented "unknown filter ⇒ empty" shape — no 404)."""
        _seed_job(engine, instance_id="m-1")
        resp = client.get(
            "/api/jobs", params={"mission_id": "m-does-not-exist"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["jobs"] == []


# ─── W-3/W-4: count-vs-page symmetry under a row-count-asymmetric page ────


@pytest.fixture
def row_count_asymmetric_page(engine: Engine) -> dict:
    """W-3/W-4 (second-pass review fold, 2026-09-07): seed MORE
    matching rows than one page holds.

    5 jobs on ``m-1`` + 2 on ``m-2`` ⇒ with ``limit=3`` the filtered
    TOTAL (5) exceeds the PAGE size (3). The asymmetry is the whole
    point: it lets total-vs-page assertions discriminate the COUNT
    query from the PAGE query —

    * if the COUNT query ignored the filter ⇒ the repo-layer
      ``total`` would read 7 (not 5);
    * if the PAGE query ignored the filter (or the limit) ⇒ the rows
      would include ``m-2`` jobs (or exceed 3).

    (At the WIRE, ``total`` currently echoes the page size — the
    S-5 wiring gap, deferred — so the discrimination runs at the
    repository layer; see ``TestFilterCountPageSymmetry``.)

    Returns the seeded page-size contract for the assertions.
    """
    for _ in range(5):
        _seed_job(engine, instance_id="m-1")
    for _ in range(2):
        _seed_job(engine, instance_id="m-2")
    return {"matching": 5, "other": 2, "page_size": 3}


class TestFilterCountPageSymmetry:
    """The ``mission_id`` filter is applied SYMMETRICALLY to the
    count query and the page query — the filtered set's ``total`` and
    its returned rows always describe the same filtered set.

    Layer note: the WIRE ``JobListResponse.total`` currently ECHOES
    the page (``total=len(job_responses)``, ``jobs_crud.py`` — the
    S-5 wiring gap, explicitly DEFERRED by the second-pass review).
    The total-vs-page DISCRIMINATION the W-3/W-4 fixture exists for
    is therefore only reachable at the REPOSITORY layer, where
    ``list()`` returns the count-query ``total`` and the page rows
    separately. The wire test below pins the current echo behavior
    so S-5's landing flips exactly one assertion here.
    """

    def test_count_and_page_legs_symmetric_at_repository(
        self,
        job_repo: JobRepository,
        engine: Engine,
        row_count_asymmetric_page: dict,
    ) -> None:
        """Repo: ``instance_id='m-1', limit=3`` ⇒ ``total == 5`` (the
        COUNT leg respected the filter: NOT 7) while the page holds
        exactly 3 rows, all on ``m-1`` (the PAGE leg respected both
        the filter and the limit)."""
        jobs, total = job_repo.list(instance_id="m-1", limit=3)
        assert total == row_count_asymmetric_page["matching"], (
            "count query must respect mission_id — a total of 7 here "
            "means the COUNT query dropped the filter"
        )
        assert total != len(jobs), (
            "fixture invariant broken: the asymmetric seed exists so "
            "total and page size differ (5 vs 3)"
        )
        assert len(jobs) == row_count_asymmetric_page["page_size"]
        assert {j.instance_id for j in jobs} == {"m-1"}, (
            "page query must respect mission_id — m-2 rows here mean "
            "the PAGE query dropped the filter"
        )

    def test_wire_total_echoes_page_under_current_wiring(
        self,
        client: TestClient,
        engine: Engine,
        row_count_asymmetric_page: dict,
    ) -> None:
        """Wire: ``?mission_id=m-1&limit=3`` returns 3 rows, all on
        ``m-1``, and ``total`` ECHOES the page (3) — the current
        S-5-deferred wiring (``total=len(job_responses)``), NOT the
        SQL count (5).

        S-5 FOLLOW-UP MARKER: when the SQL count is wired through to
        ``JobListResponse.total``, this assertion flips to
        ``body["total"] == 5`` — at which point the wire joins the
        repository in discriminating the two legs (see
        ``test_count_and_page_legs_symmetric_at_repository``).
        """
        resp = client.get(
            "/api/jobs",
            params={"mission_id": "m-1", "limit": 3},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["jobs"]) == row_count_asymmetric_page["page_size"]
        assert {j["instance_id"] for j in body["jobs"]} == {"m-1"}
        assert body["total"] == len(body["jobs"]), (
            "wire total currently echoes the page (S-5 deferred); if "
            "this fires, S-5 landed — flip the expectation to "
            "body['total'] == 5 and re-discriminate at the wire"
        )

    def test_last_page_drains_to_exactly_total(
        self, job_repo: JobRepository,
        row_count_asymmetric_page: dict,
    ) -> None:
        """Paging drains the filtered set to exactly ``total`` —
        3 + 2 = 5 — proving both legs track the same filtered set
        across page boundaries.

        Repo-layer (the wire route exposes ``limit`` but no
        ``offset`` query param — pagination continuation lives at the
        repository seam, which the router itself does not use yet).
        """
        first_jobs, first_total = job_repo.list(
            instance_id="m-1", limit=3, offset=0
        )
        rest_jobs, rest_total = job_repo.list(
            instance_id="m-1", limit=3, offset=3
        )
        assert first_total == rest_total == 5
        assert len(first_jobs) == 3
        assert len(first_jobs) + len(rest_jobs) == 5
        assert {
            j.instance_id for j in first_jobs + rest_jobs
        } == {"m-1"}


# ─── S-2: engine-bound query-count pin for the filtered list path ─────────


class TestJobsMissionFilterQueryCount:
    """S-2 (second-pass review fold, 2026-09-07): the ``mission_id``
    filter adds NO extra SELECT vs the unfiltered path.

    Ports the engine-listener pattern from
    ``tests/unit/routers/test_missions_api.py::
    TestEngineBoundQueryCount`` to the jobs mission_id filter path:
    a ``before_cursor_execute`` spy counts SELECT statements against
    the engine DURING the HTTP request. Mock counting is banned for
    this contract.

    Harness note: rows are seeded with ``admission_state='done'`` so
    the router's per-row enrichment legs (queue-position probe for
    QUEUED rows, DLQ lookup for DEAD rows) never fire — the baseline
    is the pure ``JobRepository.list`` shape: exactly TWO SELECTs
    (count + page, both on ``job_queue_items``), FLAT regardless of
    seed size. The unfiltered baseline count is pinned EXACTLY; the
    filtered request must issue the SAME count.
    """

    SEEDS_PER_MISSION = 5
    OTHER_MISSION_SEEDS = 3

    @staticmethod
    def _count_selects(engine: Engine):
        """Attach the spy; returns ``(counts, detach)`` where
        ``counts`` is ``{"total": n, "job_queue_items": m}`` —
        captured by reference and valid between attach and detach."""
        counts = {"total": 0, "job_queue_items": 0}

        def _before_cursor_execute(  # noqa: ANN001 — SQLAlchemy hook
            conn, cursor, statement, parameters, context, executemany  # noqa: ARG001
        ):
            s = statement.strip().upper()
            if s.startswith("SELECT"):
                counts["total"] += 1
                if "JOB_QUEUE_ITEMS" in s:
                    counts["job_queue_items"] += 1

        event.listen(engine, "before_cursor_execute", _before_cursor_execute)

        def _detach() -> None:
            event.remove(
                engine, "before_cursor_execute", _before_cursor_execute
            )

        return counts, _detach

    def _seed_settled(self, engine: Engine) -> None:
        for _ in range(self.SEEDS_PER_MISSION):
            _seed_job(
                engine,
                instance_id="m-1",
                admission_state=AdmissionState.DONE.value,
                terminal_reason="completed",
            )
        for _ in range(self.OTHER_MISSION_SEEDS):
            _seed_job(
                engine,
                instance_id="m-2",
                admission_state=AdmissionState.DONE.value,
                terminal_reason="completed",
            )

    def test_filter_baseline_is_two_selects(
        self, client: TestClient, engine: Engine
    ) -> None:
        """Unfiltered baseline pins at EXACTLY 2 SELECTs (count +
        page); the filtered request issues the SAME 2 — the filter is
        a WHERE-clause narrowing on the existing queries, never an
        extra round-trip."""
        self._seed_settled(engine)

        counts, detach = self._count_selects(engine)
        try:
            resp = client.get("/api/jobs")
        finally:
            detach()
        assert resp.status_code == 200
        assert counts["total"] == 2, (
            f"unfiltered baseline must be exactly 2 SELECTs (count + "
            f"page); got {counts} — an enrichment leg fired on this "
            "seed shape"
        )
        assert counts["job_queue_items"] == 2, counts

        counts, detach = self._count_selects(engine)
        try:
            resp = client.get("/api/jobs", params={"mission_id": "m-1"})
        finally:
            detach()
        assert resp.status_code == 200
        assert len(resp.json()["jobs"]) == self.SEEDS_PER_MISSION
        assert counts["total"] == 2, (
            f"filtered request must stay at the 2-SELECT baseline "
            f"(the filter must add NO extra SELECT); got {counts}"
        )
        assert counts["job_queue_items"] == 2, counts

    def test_filter_select_count_flat_as_seeds_double(
        self, client: TestClient, engine: Engine
    ) -> None:
        """The 2-SELECT bound is FLAT: doubling the seed set does not
        grow the filtered path's SELECT count (no N+1)."""
        for multiplier in (1, 2):
            # Fresh seed set per multiplier — the row count doubles.
            for _ in range(self.SEEDS_PER_MISSION * multiplier):
                _seed_job(
                    engine,
                    instance_id=f"m-flat-{multiplier}",
                    admission_state=AdmissionState.DONE.value,
                    terminal_reason="completed",
                )

            counts, detach = self._count_selects(engine)
            try:
                resp = client.get(
                    "/api/jobs",
                    params={"mission_id": f"m-flat-{multiplier}"},
                )
            finally:
                detach()

            assert resp.status_code == 200
            assert (
                len(resp.json()["jobs"])
                == self.SEEDS_PER_MISSION * multiplier
            )
            assert counts["total"] == 2, (
                f"SELECT count must stay flat at 2 as the seed set "
                f"doubles (multiplier={multiplier}); got {counts}"
            )
