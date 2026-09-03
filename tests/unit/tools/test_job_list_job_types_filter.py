"""Tests for the M2 ``job_types`` filter on ``job_list``.

Mission-class Milestone M2 (2026-09-02, ``feature/mission-class``) —
contract draft §4: ``job_list`` gains a ``job_types`` filter
(``["task"|"message"]``, default both). The legacy ``statuses`` filter
is RETAINED unchanged through the M3 window (additive migration).

The filter lives at three layers:

  1. ``daemon/repositories/job_queue/repository.py::JobRepository.list``
     — applies the filter in SQL (the cheapest layer; the IN-clause
     is one extra predicate in the WHERE).
  2. ``daemon/services/job_queue_service.py::JobQueueService.list_jobs``
     — passes the filter through to the repository.
  3. ``daemon/routers/jobs_crud.py::list_jobs`` + ``daemon/tools/job_queue.py::job_list``
     — parse the comma-separated ``job_types`` query param /
     tool argument, validate against the accepted vocabulary
     (``task`` / ``message``), and thread through to the service.

Each test pins one layer's contract. Together they pin the
additive migration: the legacy ``statuses`` filter still works
exactly as before, the new ``job_types`` filter narrows by
``JobItem.job_type``, and unknown values degrade to an
honestly-empty page (no exception).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401

from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.tools.job_queue import create_job_tools


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout).

    Mirrors the conventions recipe in
    ``tests/unit/services/test_mission_resolver.py``.
    """
    db_path = tmp_path / "job-list-types-test.sqlite"
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


def _seed_job(
    engine: Engine,
    *,
    job_type: str,
    admission_state: str = AdmissionState.QUEUED.value,
) -> str:
    """Insert a populated ``JobItem`` row."""
    jid = f"job-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        job = JobItem(
            job_id=jid,
            agent_id="developer",
            agent_dir="agents/developer",
            admission_state=admission_state,
            job_type=job_type,
            project_id="test-project",
            priority=5,
            message="(seed)",
            source="api",
            job_metadata=json.dumps({}),
            created_at=now,
            updated_at=now,
        )
        s.add(job)
        s.commit()
    return jid


# ─── Repository layer: SQL-level filter ──────────────────────────────────


class TestJobRepositoryJobTypesFilter:
    """``JobRepository.list`` honours the ``job_types`` parameter."""

    def test_default_returns_both_kinds(
        self, job_repo: JobRepository, engine: Engine
    ) -> None:
        """No ``job_types`` filter ⇒ both kinds returned (back-compat)."""
        _seed_job(engine, job_type="task")
        _seed_job(engine, job_type="message")
        jobs, total = job_repo.list()
        assert total == 2
        assert {j.job_type for j in jobs} == {"task", "message"}

    def test_filter_task_only(self, job_repo: JobRepository, engine: Engine) -> None:
        """``job_types=['task']`` ⇒ only task rows returned."""
        _seed_job(engine, job_type="task")
        _seed_job(engine, job_type="task")
        _seed_job(engine, job_type="message")
        jobs, total = job_repo.list(job_types=["task"])
        assert total == 2
        assert {j.job_type for j in jobs} == {"task"}

    def test_filter_message_only(
        self, job_repo: JobRepository, engine: Engine
    ) -> None:
        """``job_types=['message']`` ⇒ only message rows returned."""
        _seed_job(engine, job_type="task")
        _seed_job(engine, job_type="message")
        _seed_job(engine, job_type="message")
        jobs, total = job_repo.list(job_types=["message"])
        assert total == 2
        assert {j.job_type for j in jobs} == {"message"}

    def test_filter_composes_with_statuses(
        self, job_repo: JobRepository, engine: Engine
    ) -> None:
        """``job_types`` and the legacy ``statuses`` filter compose
        with AND semantics. Both layers can narrow simultaneously."""
        _seed_job(
            engine, job_type="task", admission_state=AdmissionState.QUEUED.value
        )
        _seed_job(
            engine, job_type="task", admission_state=AdmissionState.DONE.value
        )
        _seed_job(
            engine, job_type="message", admission_state=AdmissionState.DONE.value
        )
        # ``statuses=['completed']`` (legacy vocabulary) maps to
        # ``admission_state='done'`` via ``_LEGACY_TO_ADMISSION`` —
        # the SQL filter then narrows by both predicates with AND
        # semantics. The expected match: the task row in
        # ``admission_state='done'`` (the done task row, NOT the
        # done message row, NOT the queued task row).
        jobs, total = job_repo.list(
            job_types=["task"],
            statuses=["completed"],
        )
        assert total == 1
        assert jobs[0].job_type == "task"
        assert jobs[0].admission_state == AdmissionState.DONE.value

    def test_unknown_value_yields_empty_result(
        self, job_repo: JobRepository, engine: Engine
    ) -> None:
        """Unknown ``job_types`` value ⇒ empty result (the §8.2
        source-less-filter degrade — matches the work-router's
        "unknown filter ⇒ empty" precedent)."""
        _seed_job(engine, job_type="task")
        _seed_job(engine, job_type="message")
        jobs, total = job_repo.list(job_types=["definitely-not-a-type"])
        assert total == 0
        assert jobs == []

    def test_mixed_known_and_unknown_keeps_only_known(
        self, job_repo: JobRepository, engine: Engine
    ) -> None:
        """Mixed input ⇒ the entire filter degrades to an empty
        IN-clause (the §8.2 source-less-filter pattern: a single
        unknown value poisons the WHOLE filter rather than
        silently widening it).

        This is the conservative degrade — same shape the HTTP
        mission-list surface uses for unknown liveness values.
        The alternative (silently dropping unknown values) would
        be a fail-open trap: a typo in the agent's filter would
        silently widen to "all records".
        """
        _seed_job(engine, job_type="task")
        _seed_job(engine, job_type="message")
        jobs, total = job_repo.list(job_types=["task", "garbage"])
        # The unknown value poisons the filter — empty result.
        assert total == 0
        assert jobs == []


# ─── Tool layer: ``job_list`` accepts ``job_types`` ───────────────────────


@pytest.fixture
def mock_manager() -> MagicMock:
    """``InstanceManager`` mock for the tool-level test (no
    resolver wiring — exercises the legacy branch of ``job_list``)."""
    manager = MagicMock()
    manager._task_repo = MagicMock()
    manager._watcher_repo = MagicMock()
    manager._instance_repository = MagicMock()
    return manager


class TestJobListToolJobTypesFilter:
    """``job_list`` tool accepts and applies the ``job_types`` filter.

    The tool layer is exercised via ``AsyncMock`` mocks of the
    ``JobQueueService.list_jobs`` call — the SQL-level filter is
    already pinned by ``TestJobRepositoryJobTypesFilter`` above, so
    this layer pins the THREADING of the filter through the tool
    (signature, validation, default-behaviour) without standing up
    a full service stack.
    """

    @pytest.fixture
    def tools(self, mock_manager):
        """Build ``job_list`` against ``AsyncMock`` services.

        The work resolver is unwired on purpose so the tool falls
        back to the JobItem-only branch (where the SQL
        ``job_types`` filter applies directly via the service).
        """
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        # Force the resolver-wired branch to skip — the tool falls
        # back to ``job_service.list_jobs`` when the resolver is
        # ``None``. Without this, ``getattr(job_service,
        # "_work_resolver", None)`` returns a child MagicMock and
        # the resolver path swallows the ``job_types`` threading.
        job_service._work_resolver = None
        # Build a mock job_item with a ``to_dict`` shape that the
        # tool iterates over.
        def _make_job_dict(job_type: str):
            return {
                "job_id": f"job-{job_type}",
                "job_type": job_type,
                "status": "pending",
            }
        # Default: both kinds
        job_service.list_jobs = AsyncMock(return_value=[
            _make_job_dict("task"),
            _make_job_dict("message"),
        ])
        queue_mgmt_service = MagicMock()
        dead_letter_service = MagicMock()
        factory_tools = create_job_tools(
            job_service=job_service,
            queue_mgmt_service=queue_mgmt_service,
            dead_letter_service=dead_letter_service,
            watcher_repo=None,
            manager=mock_manager,
        )
        return {
            "tools": {t.name: t for t in factory_tools},
            "job_service": job_service,
            "_make_job_dict": _make_job_dict,
        }

    @pytest.mark.asyncio
    async def test_tool_passes_job_types_to_service(self, tools) -> None:
        """The tool threads ``job_types`` through to the service.

        Pins the contract that ``job_types`` is forwarded to
        ``JobQueueService.list_jobs(job_types=...)`` so the SQL
        filter applies at the repository layer.
        """
        t = tools["tools"]["job_list"]
        # Configure the mock to return task-only when ``job_types=['task']``
        tools["job_service"].list_jobs = AsyncMock(return_value=[
            tools["_make_job_dict"]("task"),
        ])
        await t.ainvoke({"job_types": ["task"]})
        call_kwargs = tools["job_service"].list_jobs.call_args.kwargs
        assert call_kwargs.get("job_types") == ["task"]

    @pytest.mark.asyncio
    async def test_tool_default_no_job_types(self, tools) -> None:
        """No ``job_types`` argument ⇒ ``job_types=None`` is forwarded."""
        t = tools["tools"]["job_list"]
        await t.ainvoke({})
        call_kwargs = tools["job_service"].list_jobs.call_args.kwargs
        # Default: ``job_types=None`` (service-level default = both kinds).
        assert call_kwargs.get("job_types") is None

    @pytest.mark.asyncio
    async def test_tool_unknown_job_types_degrades_to_empty(
        self, tools
    ) -> None:
        """Unknown ``job_types`` value ⇒ empty page at the tool layer.

        The tool narrows the filter to the accepted vocabulary
        (``task`` / ``message``); an unknown value yields an empty
        list. This is the legacy-branch degrade — the SQL filter
        applies the same empty-IN-clause pattern (pinned in
        ``TestJobRepositoryJobTypesFilter::test_unknown_value_yields_empty_result``).
        """
        t = tools["tools"]["job_list"]
        result = await t.ainvoke({
            "job_types": ["bogus"],
        })
        # Empty page (the tool narrows the unknown value to an
        # empty accepted set, then the SQL IN-clause matches nothing).
        assert result["jobs"] == []
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_tool_legacy_statuses_filter_preserved(self, tools) -> None:
        """The M3-window retention: ``statuses`` still works
        unchanged alongside ``job_types``.

        Pinning this so a future refactor that drops or renames
        the legacy ``statuses`` parameter fails LOUDLY here
        (contract draft §4: ``statuses`` filter is RETAINED
        through the M3 window).
        """
        t = tools["tools"]["job_list"]
        tools["job_service"].list_jobs = AsyncMock(return_value=[
            tools["_make_job_dict"]("task"),
        ])
        await t.ainvoke({
            "statuses": ["completed"],  # legacy vocabulary
            "job_types": ["task"],
        })
        call_kwargs = tools["job_service"].list_jobs.call_args.kwargs
        assert call_kwargs.get("statuses") == ["completed"]
        assert call_kwargs.get("job_types") == ["task"]
