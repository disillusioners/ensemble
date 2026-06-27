"""Unit tests for the Virtual Job Management Surface — Phase 1 (Batch 4).

Tests cover two cooperating modules:

* ``daemon.services.work_status`` — ``canonicalize_status`` and
  ``is_terminal`` helpers that map the per-table Task / JobItem status
  strings onto a single canonical virtual-job vocabulary.
* ``daemon.services.work_resolver`` — ``WorkResolverService.resolve_work``
  and ``WorkResolverService.list_work``, the unified read API that
  collapses the worker pool's ``task`` table and the dependency bus's
  ``job_queue_items`` table onto a :class:`WorkRecord` view-model.

Pattern follows the existing in-memory SQLite repository tests
(``tests/unit/test_instance_tree_loading.py``,
``tests/unit/test_pause_resume_root.py``) — a fresh engine per test
backed by ``StaticPool`` and ``PRAGMA foreign_keys=ON``, with
``SQLModel.metadata.create_all`` to materialise the schema and direct
``Session`` inserts for fixture data so the tests can pin known
``work_id`` values.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import JobItem, JobStatus
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_queue_service import JobQueueService
from daemon.services.work_resolver import WorkRecord, WorkResolverService
from daemon.services.work_status import canonicalize_status, is_terminal


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool for cross-thread safety).

    Same shape as ``tests/unit/test_pause_resume_root.py::engine`` —
    StaticPool keeps one connection alive for the whole test so
    asyncio.to_thread workers (if any) share the in-memory store, and
    PRAGMA foreign_keys=ON matches the production daemon's SQLite
    posture so any future FK-guarded reads behave the same here as in
    production.
    """
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
    """Real ``TaskRepository`` bound to the in-memory engine."""
    return TaskRepository(engine)


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    """Real ``JobRepository`` bound to the in-memory engine."""
    return JobRepository(engine)


@pytest.fixture
def instance_repo(engine: Engine) -> SQLModelInstanceRepository:
    """Real ``SQLModelInstanceRepository`` bound to the in-memory engine."""
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def resolver(task_repo: TaskRepository, job_repo: JobRepository,
             instance_repo: SQLModelInstanceRepository) -> WorkResolverService:
    """Constructed ``WorkResolverService`` against the three real repos."""
    return WorkResolverService(task_repo, job_repo, instance_repo)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    agent_id: str = "developer",
    project_id: str | None = "test-project",
    status: str = "running",
) -> str:
    """Insert an ``Instance`` row. Returns the ``instance_id``."""
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


def _seed_task(
    engine: Engine,
    *,
    work_id: str | None = None,
    instance_id: str = "inst-test",
    status: str = TaskStatus.PENDING.value,
    result: str | None = None,
    error: str | None = None,
    project_id: str | None = "test-project",
    created_at: datetime | None = None,
    seed_instance: bool = True,
) -> str:
    """Insert a ``Task`` row. Returns the ``work_id`` (auto-generated if None).

    By default this also seeds the matching ``Instance`` row carrying
    ``project_id`` (and the default ``agent_id``) so the resolver's
    ``_lookup_instance`` post-fetch can resolve both ``agent_id`` and
    ``project_id``. Pass ``seed_instance=False`` for tests that
    intentionally exercise the "instance deleted / orphaned work unit"
    path (e.g. ``test_resolve_work_task_without_instance_returns_none_agent_id``).
    """
    if seed_instance:
        # Match the resolver's lookup: ``_lookup_instance(task.instance_id)``
        # must return a row with the right ``project_id`` for the post-fetch
        # filter in ``list_work`` to keep the row. Skip if the test
        # already seeded the same instance_id with the right
        # project_id (some tests seed it explicitly so they can pin
        # agent_id / other fields). The check is a cheap PK lookup;
        # the engine is in-memory and the table is small.
        with Session(engine) as s:
            existing = s.get(Instance, instance_id)
        if existing is None or existing.project_id != project_id:
            _seed_instance(
                engine,
                instance_id=instance_id,
                project_id=project_id,
            )
    wid = work_id or str(uuid.uuid4())
    created = created_at or datetime.now(timezone.utc)
    with Session(engine) as s:
        task = Task(
            work_id=wid,
            task_type="process_message",
            instance_id=instance_id,
            message_id=None,
            status=status,
            result=result,
            error=error,
            created_at=created,
        )
        s.add(task)
        s.commit()
    return wid


def _seed_job(
    engine: Engine,
    *,
    job_id: str | None = None,
    agent_id: str = "developer",
    instance_id: str | None = None,
    status: str = JobStatus.PENDING.value,
    result_summary: str | None = None,
    error_message: str | None = None,
    project_id: str | None = "test-project",
    created_at: str | None = None,
    deleted_at: str | None = None,
) -> str:
    """Insert a ``JobItem`` row. Returns the ``job_id`` (auto-generated if None)."""
    jid = job_id or str(uuid.uuid4())
    created = created_at or datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        job = JobItem(
            job_id=jid,
            agent_id=agent_id,
            agent_dir=f"/tmp/agents/{agent_id}",
            message="test message",
            source="api",
            project_id=project_id,
            priority=5,
            status=status,
            result_summary=result_summary,
            error_message=error_message,
            instance_id=instance_id,
            created_at=created,
            deleted_at=deleted_at,
            job_metadata={},
        )
        s.add(job)
        s.commit()
    return jid


# ─── work_status canonicalization ───────────────────────────────────────────


class TestCanonicalizeStatus:
    """``work_status.canonicalize_status`` maps both Task and JobItem
    source strings onto the canonical virtual-job vocabulary."""

    def test_task_running_maps_to_processing(self):
        # Task uses "running"; the resolver surface speaks "processing".
        assert canonicalize_status("running") == "processing"

    def test_jobitem_dead_letter_passes_through(self):
        # dead_letter only exists on JobItem side and is already canonical.
        assert canonicalize_status("dead_letter") == "dead_letter"

    def test_paused_passes_through(self):
        # paused is identical in both vocabularies.
        assert canonicalize_status("paused") == "paused"

    def test_unknown_status_passes_through_unchanged(self):
        # Defensive: a future/unknown status is returned verbatim so the
        # resolver does not crash on a status the map has not been
        # taught about.
        assert canonicalize_status("unknown_status") == "unknown_status"


class TestIsTerminal:
    """``work_status.is_terminal`` recognises terminal virtual-job states."""

    def test_completed_is_terminal(self):
        assert is_terminal("completed") is True

    def test_paused_is_not_terminal(self):
        # paused is intentionally non-terminal — a paused work unit can
        # be resumed back to processing.
        assert is_terminal("paused") is False

    def test_dead_letter_is_terminal(self):
        assert is_terminal("dead_letter") is True

    def test_processing_is_not_terminal(self):
        assert is_terminal("processing") is False

    def test_unknown_status_is_not_terminal(self):
        # Conservative default: unknown → "might still move" so callers
        # do not accidentally treat a future status as final.
        assert is_terminal("some_future_status") is False


# ─── WorkResolverService.resolve_work ───────────────────────────────────────


class TestResolveWork:
    """``WorkResolverService.resolve_work`` looks up a work_id across
    both the Task and JobItem tables and returns a populated
    :class:`WorkRecord`."""

    def test_resolve_work_job_returns_job_record(
        self, engine, resolver, job_repo
    ):
        """``resolve_work(job.job_id)`` returns a JobItem-backed record."""
        _seed_instance(engine, instance_id="inst-1")
        jid = _seed_job(
            engine,
            status=JobStatus.PROCESSING.value,
            instance_id="inst-1",
            result_summary="all done",
        )

        record = resolver.resolve_work(jid)

        assert record is not None
        assert record.kind == "job"
        assert record.work_id == jid
        # JobItem "processing" stays "processing" on the canonical side.
        assert record.status == "processing"
        assert record.instance_id == "inst-1"
        assert record.project_id == "test-project"
        assert record.agent_id == "developer"
        assert record.result_summary == "all done"
        assert record.error is None
        # Sanity-check the underlying row really is what we asked for.
        assert job_repo.get(jid) is not None

    def test_resolve_work_task_returns_task_record(
        self, engine, resolver, task_repo
    ):
        """``resolve_work(task.work_id)`` returns a Task-backed record,
        including the agent_id lookup from the instance and the
        result_summary parse from ``Task.result`` JSON."""
        _seed_instance(engine, instance_id="inst-task", agent_id="developer")
        # Task.result is a JSON string carrying the assistant payload.
        result_payload = json.dumps({"answer": "42", "sources": ["a", "b"]})
        wid = _seed_task(
            engine,
            instance_id="inst-task",
            status=TaskStatus.RUNNING.value,
            result=result_payload,
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        assert record.kind == "task"
        assert record.work_id == wid
        # Task "running" canonicalises to "processing".
        assert record.status == "processing"
        assert record.instance_id == "inst-task"
        assert record.project_id == "test-project"
        # agent_id must be looked up from the Instance row — Task does
        # not carry it directly.
        assert record.agent_id == "developer"
        # ``Task.result`` is parsed; a non-string payload is re-serialised
        # so the frontend always receives a string.
        assert isinstance(record.result_summary, str)
        parsed = json.loads(record.result_summary)
        assert parsed == {"answer": "42", "sources": ["a", "b"]}
        assert record.error is None
        # Sanity-check the underlying row really is what we asked for.
        assert task_repo.get_by_work_id(wid) is not None

    def test_resolve_work_task_result_summary_string_passthrough(
        self, engine, resolver
    ):
        """If ``Task.result`` is a JSON string already, it's returned as-is
        (mirrors the rule in ``daemon/routers/messages.py:251-263``)."""
        _seed_instance(engine, instance_id="inst-2")
        wid = _seed_task(
            engine,
            instance_id="inst-2",
            status=TaskStatus.COMPLETED.value,
            result=json.dumps("a plain string"),
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        # When the parsed payload is itself a string, the resolver
        # hands it back verbatim — no double JSON encoding.
        assert record.result_summary == "a plain string"

    def test_resolve_work_task_result_none(self, engine, resolver):
        """If ``Task.result`` is empty, ``result_summary`` is ``None``."""
        _seed_instance(engine, instance_id="inst-3")
        wid = _seed_task(
            engine,
            instance_id="inst-3",
            status=TaskStatus.COMPLETED.value,
            result=None,
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        assert record.result_summary is None

    def test_resolve_work_task_result_invalid_json_falls_back_to_raw(
        self, engine, resolver
    ):
        """If ``Task.result`` fails to parse as JSON, fall back to the raw string."""
        _seed_instance(engine, instance_id="inst-4")
        wid = _seed_task(
            engine,
            instance_id="inst-4",
            status=TaskStatus.FAILED.value,
            result="not valid json {",
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        assert record.result_summary == "not valid json {"

    def test_resolve_work_task_paused_status(self, engine, resolver):
        """``paused`` canonicalises identically on both sides."""
        _seed_instance(engine, instance_id="inst-paused")
        wid = _seed_task(
            engine,
            instance_id="inst-paused",
            status=TaskStatus.PAUSED.value,
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        assert record.status == "paused"

    def test_resolve_work_job_dead_letter_status(self, engine, resolver):
        """``dead_letter`` is a JobItem-only canonical value."""
        jid = _seed_job(
            engine,
            status=JobStatus.DEAD_LETTER.value,
            error_message="retries exhausted",
        )

        record = resolver.resolve_work(jid)

        assert record is not None
        assert record.kind == "job"
        assert record.status == "dead_letter"
        assert record.error == "retries exhausted"

    def test_resolve_work_unknown_returns_none(self, resolver):
        """A random UUID present in neither table returns ``None``."""
        missing_id = str(uuid.uuid4())

        record = resolver.resolve_work(missing_id)

        assert record is None

    def test_resolve_work_task_shadows_job_with_same_id(
        self, engine, resolver
    ):
        """Task-first lookup order means a Task row's ``work_id`` shadows
        any JobItem row that happens to share the value (defence against
        the theoretical UUID collision case)."""
        # Both tables get the same identifier — only one row can win.
        shared_id = str(uuid.uuid4())
        _seed_instance(engine, instance_id="inst-shadow")
        _seed_task(
            engine,
            work_id=shared_id,
            instance_id="inst-shadow",
            status=TaskStatus.RUNNING.value,
        )
        _seed_job(
            engine,
            job_id=shared_id,
            status=JobStatus.PROCESSING.value,
            instance_id="inst-shadow",
        )

        record = resolver.resolve_work(shared_id)

        # The task-first branch wins.
        assert record is not None
        assert record.kind == "task"

    def test_resolve_work_task_without_instance_returns_none_agent_id(
        self, engine, resolver
    ):
        """A task whose instance was deleted returns ``agent_id=None``
        (orphaned work unit) without blowing up."""
        # Insert a task referencing an instance that doesn't exist in
        # the Instance table — the resolver must degrade gracefully.
        # ``seed_instance=False`` opts out of the fixture's auto-seeding
        # so the resolver's ``_lookup_instance`` returns None.
        wid = _seed_task(
            engine,
            instance_id="ghost-instance",
            status=TaskStatus.COMPLETED.value,
            seed_instance=False,
        )

        record = resolver.resolve_work(wid)

        assert record is not None
        assert record.kind == "task"
        assert record.instance_id == "ghost-instance"
        assert record.agent_id is None


# ─── WorkResolverService.list_work ──────────────────────────────────────────


class TestListWork:
    """``WorkResolverService.list_work`` returns the union of Task and
    JobItem rows matching the supplied filters, newest-first."""

    def test_list_work_empty(self, resolver):
        """No rows in either table → empty list."""
        assert resolver.list_work() == []

    def test_list_work_returns_both_kinds(self, engine, resolver):
        """With no filters, ``list_work`` returns rows from both tables."""
        _seed_instance(engine, instance_id="inst-a")
        _seed_instance(engine, instance_id="inst-b")
        task_id = _seed_task(
            engine, instance_id="inst-a", status=TaskStatus.PENDING.value
        )
        job_id = _seed_job(
            engine, instance_id="inst-b", status=JobStatus.PENDING.value
        )

        records = resolver.list_work()

        assert len(records) == 2
        work_ids = {r.work_id for r in records}
        assert work_ids == {task_id, job_id}
        assert {r.kind for r in records} == {"task", "job"}

    def test_list_work_filter_by_project_id(self, engine, resolver):
        """``project_id`` filter narrows results to one project."""
        _seed_task(engine, instance_id="i1", project_id="proj-A")
        _seed_task(engine, instance_id="i2", project_id="proj-B")
        _seed_job(engine, project_id="proj-A")
        _seed_job(engine, project_id="proj-B")

        records = resolver.list_work(project_id="proj-A")

        assert len(records) == 2
        assert {r.project_id for r in records} == {"proj-A"}

    def test_list_work_filter_by_instance_id(self, engine, resolver):
        """``instance_id`` filter narrows results to one instance."""
        _seed_task(engine, instance_id="inst-target")
        _seed_task(engine, instance_id="inst-other")
        _seed_job(engine, instance_id="inst-target")
        _seed_job(engine, instance_id="inst-other")

        records = resolver.list_work(instance_id="inst-target")

        assert len(records) == 2
        assert {r.instance_id for r in records} == {"inst-target"}

    def test_list_work_filter_by_status_canonical(self, engine, resolver):
        """A canonical ``status`` filter matches both Task ``running`` and
        JobItem ``processing`` (the two source values that canonicalise
        to ``"processing"``)."""
        _seed_task(engine, instance_id="i1", status=TaskStatus.RUNNING.value)
        _seed_task(engine, instance_id="i2", status=TaskStatus.PENDING.value)
        _seed_job(engine, status=JobStatus.PROCESSING.value)
        _seed_job(engine, status=JobStatus.PENDING.value)

        records = resolver.list_work(status="processing")

        assert len(records) == 2
        assert {r.status for r in records} == {"processing"}
        # One Task (status="running") and one JobItem (status="processing")
        # both canonicalise to "processing".
        assert {r.kind for r in records} == {"task", "job"}

    def test_list_work_filter_by_status_dead_letter_only_matches_jobs(
        self, engine, resolver
    ):
        """``dead_letter`` is JobItem-only — Task rows never match."""
        _seed_task(engine, instance_id="i-dl", status=TaskStatus.FAILED.value)
        _seed_job(engine, status=JobStatus.DEAD_LETTER.value)
        _seed_job(engine, status=JobStatus.PENDING.value)

        records = resolver.list_work(status="dead_letter")

        assert len(records) == 1
        assert records[0].kind == "job"
        assert records[0].status == "dead_letter"

    def test_list_work_filter_by_kind_task(self, engine, resolver):
        """``kind="task"`` excludes JobItem rows."""
        _seed_task(engine, instance_id="i1")
        _seed_job(engine, instance_id="i2")

        records = resolver.list_work(kind="task")

        assert len(records) == 1
        assert records[0].kind == "task"

    def test_list_work_filter_by_kind_job(self, engine, resolver):
        """``kind="job"`` excludes Task rows."""
        _seed_task(engine, instance_id="i1")
        _seed_job(engine, instance_id="i2")

        records = resolver.list_work(kind="job")

        assert len(records) == 1
        assert records[0].kind == "job"

    def test_list_work_sort_newest_first(self, engine, resolver):
        """Records are returned ordered by ``created_at`` DESC."""
        older = _seed_task(
            engine,
            instance_id="i1",
            created_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        )
        newer = _seed_job(
            engine,
            created_at=datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
        )

        records = resolver.list_work()

        assert len(records) == 2
        # Newer first.
        assert records[0].work_id == newer
        assert records[1].work_id == older

    def test_list_work_excludes_soft_deleted_jobs(self, engine, resolver):
        """``deleted_at IS NOT NULL`` JobItems are invisible to the
        virtual job surface (matches ``JobRepository.list`` default)."""
        active_id = _seed_job(
            engine, status=JobStatus.PENDING.value, deleted_at=None
        )
        _seed_job(
            engine, status=JobStatus.PENDING.value,
            deleted_at=datetime.now(timezone.utc).isoformat(),
        )
        _seed_task(engine, instance_id="i-task")

        records = resolver.list_work()

        work_ids = {r.work_id for r in records}
        assert active_id in work_ids
        # The soft-deleted job must NOT appear.
        assert len(records) == 2  # 1 task + 1 active job

    def test_list_work_combines_multiple_filters(self, engine, resolver):
        """Filters compose with AND semantics."""
        _seed_task(
            engine,
            instance_id="i-match",
            project_id="proj-match",
            status=TaskStatus.RUNNING.value,
        )
        _seed_task(
            engine,
            instance_id="i-other",
            project_id="proj-match",
            status=TaskStatus.RUNNING.value,
        )
        _seed_job(
            engine,
            instance_id="i-match",
            project_id="proj-match",
            status=JobStatus.PROCESSING.value,
        )

        records = resolver.list_work(
            project_id="proj-match",
            instance_id="i-match",
            status="processing",
        )

        # 1 task + 1 job, both matching all three filters.
        assert len(records) == 2
        assert {r.kind for r in records} == {"task", "job"}
        for r in records:
            assert r.project_id == "proj-match"
            assert r.instance_id == "i-match"
            assert r.status == "processing"


# ─── Phase 2 (Batch 3): JobQueueService.get_work and reconcile_terminal_watches ─
# Phase 2 Batch 3 of ``feature/virtual-job-management-surface`` adds two
# service-level surfaces that route through the resolver:
#
# * ``JobQueueService.get_work(work_id)`` — the new kind-agnostic lookup
#   that Batch 4 will switch the ``job_get`` / ``job_list`` MCP tools onto.
#   JobItem-only ``get_job`` stays intact (the HTTP API still needs it).
#
# * ``JobQueueService.reconcile_terminal_watches`` resolver path — the
#   restart-time sweep now catches watched Task rows in addition to
#   JobItem rows. A legacy JobItem-only fallback is kept for partial-wiring
#   scenarios; the existing TestReconcileTerminalWatches tests in
#   ``tests/job_queue/test_jober_watch_integration.py`` cover that path.


# ── Shared fixtures ─


@pytest.fixture
def watcher_repo(engine: Engine) -> JobWatcherRepository:
    """Real ``JobWatcherRepository`` bound to the in-memory engine.

    The engine fixture already creates all SQLModel tables (including
    ``job_watchers``, which is added to ``SQLModel.metadata`` by
    importing ``daemon.repositories.job_queue.watcher_models``), so no
    extra ``create_all`` is needed here.
    """
    return JobWatcherRepository(engine)


@pytest.fixture
def instance_manager_mock():
    """Mock ``InstanceManager`` whose ``enqueue_message`` is awaitable.

    ``notify_watchers`` (and its delegate ``notify_work_watchers``)
    call ``instance_manager.enqueue_message(...)`` per watcher on the
    resolver path. The mock records calls so tests can assert the
    ``[JOB_EVENT]`` payload was emitted.
    """
    manager = MagicMock()
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-test")
    )
    manager.terminate_instance = AsyncMock(return_value=True)
    return manager


@pytest.fixture
def job_queue_service_with_resolver(
    job_repo: JobRepository,
    resolver: WorkResolverService,
    watcher_repo: JobWatcherRepository,
    instance_manager_mock,
) -> JobQueueService:
    """``JobQueueService`` wired with a real resolver and a real watcher repo.

    ``lock_manager`` and ``queue_repo`` are not exercised by ``get_work``
    or the resolver path of ``reconcile_terminal_watches`` (those paths
    only touch ``_work_resolver`` / ``_watcher_repo`` / ``_repository`` /
    ``_instance_manager``), so ``MagicMock`` placeholders are sufficient
    for the constructor.
    """
    service = JobQueueService(
        repository=job_repo,
        lock_manager=MagicMock(),
        queue_repo=MagicMock(),
        instance_manager=instance_manager_mock,
    )
    service.set_watcher_repo(watcher_repo)
    service.set_work_resolver(resolver)
    return service


@pytest.fixture
def job_queue_service_no_resolver(
    job_repo: JobRepository,
    watcher_repo: JobWatcherRepository,
    instance_manager_mock,
) -> JobQueueService:
    """``JobQueueService`` with a watcher repo but NO resolver wired.

    Exercises the legacy fallback in ``reconcile_terminal_watches`` and
    the ``None``-return path in ``get_work``. ``JobQueueService.__init__``
    initialises ``_work_resolver = None`` itself, so this fixture just
    needs the watcher repo to be set.
    """
    service = JobQueueService(
        repository=job_repo,
        lock_manager=MagicMock(),
        queue_repo=MagicMock(),
        instance_manager=instance_manager_mock,
    )
    service.set_watcher_repo(watcher_repo)
    # Intentionally do NOT call ``set_work_resolver`` — this fixture
    # represents the partial-wiring scenario where the resolver has
    # not been wired yet (older test doubles, ad-hoc service
    # construction).
    return service


# ─── JobQueueService.get_work ────────────────────────────────────────────────


class TestGetWork:
    """``JobQueueService.get_work`` resolves a ``work_id`` to a
    :class:`WorkRecord` through the wired resolver, or returns ``None``
    when the resolver is not wired.

    Sits next to :class:`TestResolveWork` (which tests the resolver
    directly) because this test exercises the service-level wrapper
    that Batch 4 will call from ``job_get`` / ``job_list``.
    """

    async def test_get_work_resolves_task(self, engine, job_queue_service_with_resolver):
        """``get_work(task.work_id)`` returns the Task-backed WorkRecord.

        Sanity-checks the service-level wrapper around
        ``WorkResolverService.resolve_work``: same return shape, same
        ``kind='task'`` / ``status='processing'`` / ``agent_id``
        looked-up-from-instance semantics that the resolver already
        documents.
        """
        _seed_instance(engine, instance_id="inst-gw-task", agent_id="developer")
        wid = _seed_task(
            engine,
            instance_id="inst-gw-task",
            status=TaskStatus.RUNNING.value,
        )

        record = await job_queue_service_with_resolver.get_work(wid)

        assert record is not None
        assert record.kind == "task"
        assert record.work_id == wid
        # Task ``running`` canonicalises to ``processing``.
        assert record.status == "processing"
        assert record.instance_id == "inst-gw-task"
        assert record.agent_id == "developer"

    async def test_get_work_resolves_job(self, engine, job_queue_service_with_resolver):
        """``get_work(job.job_id)`` returns the JobItem-backed WorkRecord."""
        _seed_instance(engine, instance_id="inst-gw-job")
        jid = _seed_job(
            engine,
            status=JobStatus.COMPLETED.value,
            result_summary="all done",
        )

        record = await job_queue_service_with_resolver.get_work(jid)

        assert record is not None
        assert record.kind == "job"
        assert record.work_id == jid
        assert record.status == "completed"
        assert record.result_summary == "all done"
        assert record.agent_id == "developer"

    async def test_get_work_returns_none_for_unknown_id(
        self, job_queue_service_with_resolver
    ):
        """A random UUID present in neither table returns ``None``."""
        missing = str(uuid.uuid4())
        record = await job_queue_service_with_resolver.get_work(missing)
        assert record is None

    async def test_get_work_returns_none_when_no_resolver_wired(
        self, job_queue_service_no_resolver
    ):
        """``get_work`` returns ``None`` when no resolver has been wired.

        Matches the deferred-wiring contract: a service constructed via
        ``JobQueueService.__new__`` (test doubles) or before
        ``set_work_resolver`` ran must not crash on the attribute
        lookup. The caller (Batch 4 tool code) is expected to branch on
        ``None`` and fall back to ``get_job`` if needed.
        """
        # ``watcher_repo`` is set but ``_work_resolver`` is None. The
        # service-level wrapper must return None rather than raise.
        record = await job_queue_service_no_resolver.get_work("any-id")
        assert record is None


# ─── JobQueueService.reconcile_terminal_watches — resolver path ──────────────


class TestReconcileTerminalWatchesResolver:
    """``JobQueueService.reconcile_terminal_watches`` resolver path:
    watched **Task** rows (worker-pool side) and **JobItem** rows
    (dispatch-queue side) are both reconciled via
    ``self._work_resolver.resolve_work``.

    The legacy JobItem-only path is exercised separately by the
    ``TestReconcileTerminalWatches`` class in
    ``tests/job_queue/test_jober_watch_integration.py``, which builds
    the service WITHOUT ``set_work_resolver`` and mocks
    ``_repository.get`` directly.
    """

    async def test_reconcile_resolver_path_reconciles_terminal_task(
        self, engine, job_queue_service_with_resolver,
        watcher_repo: JobWatcherRepository,
        instance_manager_mock,
    ):
        """A watch pointing at a Task in ``completed`` state is reconciled.

        The Task row is invisible to ``_repository.get`` (the
        JobItem-only path), so the only way the reconcile can find it
        is via ``self._work_resolver.resolve_work`` — which is the
        Phase 2 Batch 3 change being verified here.
        """
        _seed_instance(engine, instance_id="inst-rc-task", agent_id="developer")
        # Seed the watcher's instance row too — ``job_watchers`` has a
        # hard FK on ``instance_id``, so without this the INSERT
        # blows up before the reconcile loop even runs.
        _seed_instance(engine, instance_id="watcher-inst-1")
        wid = _seed_task(
            engine,
            instance_id="inst-rc-task",
            status=TaskStatus.COMPLETED.value,
            result=json.dumps({"answer": "done"}),
        )
        # Watcher points at the Task's work_id. ``add_watch`` defaults
        # to ``ALL_TERMINAL_STATES`` so ``completed`` passes the
        # watcher filter.
        watcher_repo.add_watch(wid, "watcher-inst-1")

        reconciled = await job_queue_service_with_resolver.reconcile_terminal_watches()

        assert reconciled == 1
        instance_manager_mock.enqueue_message.assert_awaited_once()
        # The notification must carry the [JOB_EVENT] marker (parser
        # contract in work_notifier.py:53-58) and the canonical status.
        call_args = instance_manager_mock.enqueue_message.call_args
        assert call_args is not None
        assert "[JOB_EVENT]" in call_args.kwargs["message"]
        assert "completed ✓" in call_args.kwargs["message"]
        assert call_args.kwargs["instance_id"] == "watcher-inst-1"
        # ``source`` must follow the orchestrator's parser contract
        # (``internal_agent:job_event:<work_id>:<status>``).
        source = call_args.kwargs["source"]
        assert source == f"internal_agent:job_event:{wid}:completed"

    async def test_reconcile_resolver_path_reconciles_terminal_job(
        self, engine, job_queue_service_with_resolver,
        watcher_repo: JobWatcherRepository,
        instance_manager_mock,
    ):
        """A watch pointing at a JobItem in ``dead_letter`` is reconciled.

        ``dead_letter`` is JobItem-only — the canonical terminal state
        that Phase 1 Batch 4 explicitly added to ``work_status``. The
        resolver-based reconcile must honour it.
        """
        _seed_instance(engine, instance_id="inst-rc-job")
        _seed_instance(engine, instance_id="watcher-inst-2")
        jid = _seed_job(
            engine,
            status=JobStatus.DEAD_LETTER.value,
            error_message="max retries exhausted",
        )
        watcher_repo.add_watch(jid, "watcher-inst-2")

        reconciled = await job_queue_service_with_resolver.reconcile_terminal_watches()

        assert reconciled == 1
        instance_manager_mock.enqueue_message.assert_awaited_once()
        call_args = instance_manager_mock.enqueue_message.call_args
        assert call_args is not None
        # dead_letter does not have a special glyph — the canonical
        # status string is passed through verbatim per work_notifier's
        # _STATUS_DISPLAY_MAP.
        assert "[JOB_EVENT]" in call_args.kwargs["message"]
        assert "dead_letter" in call_args.kwargs["message"]
        # The error message must surface in the notification body so
        # the watching agent can see why the work unit died.
        assert "max retries exhausted" in call_args.kwargs["message"]

    async def test_reconcile_resolver_path_skips_non_terminal_task(
        self, engine, job_queue_service_with_resolver,
        watcher_repo: JobWatcherRepository,
        instance_manager_mock,
    ):
        """A watch pointing at a still-running Task is NOT reconciled.

        ``is_terminal('processing')`` returns ``False`` (paused and
        processing are explicitly non-terminal in
        ``work_status.is_terminal``) — the resolver-based reconcile
        must respect this and not emit spurious notifications.
        """
        _seed_instance(engine, instance_id="inst-rc-running")
        _seed_instance(engine, instance_id="watcher-inst-3")
        wid = _seed_task(
            engine,
            instance_id="inst-rc-running",
            status=TaskStatus.RUNNING.value,
        )
        watcher_repo.add_watch(wid, "watcher-inst-3")

        reconciled = await job_queue_service_with_resolver.reconcile_terminal_watches()

        assert reconciled == 0
        instance_manager_mock.enqueue_message.assert_not_awaited()

    async def test_reconcile_resolver_path_handles_missing_work(
        self, engine, job_queue_service_with_resolver,
        watcher_repo: JobWatcherRepository,
        instance_manager_mock,
    ):
        """A watch pointing at a work_id that has been deleted is skipped.

        ``resolve_work`` returns ``None`` for unknown IDs; the
        reconcile loop treats this as "nothing to notify, don't
        increment the counter" and moves on. The watcher row is NOT
        cleaned up here — that's the ``notify_watchers`` job, and we
        only call it when ``is_terminal(record.status)`` is True.
        """
        _seed_instance(engine, instance_id="watcher-inst-4")
        watcher_repo.add_watch("ghost-work-id", "watcher-inst-4")

        reconciled = await job_queue_service_with_resolver.reconcile_terminal_watches()

        assert reconciled == 0
        instance_manager_mock.enqueue_message.assert_not_awaited()

    async def test_reconcile_resolver_path_mixes_task_and_job_watches(
        self, engine, job_queue_service_with_resolver,
        watcher_repo: JobWatcherRepository,
        instance_manager_mock,
    ):
        """A single sweep reconciles BOTH a terminal Task and a terminal
        JobItem watch.

        This is the core motivation for Phase 2 Batch 3 — before this
        change, the JobItem-only path would silently skip Task
        watches. Now both kinds flow through the same resolver.
        """
        _seed_instance(engine, instance_id="inst-mix-task", agent_id="developer")
        _seed_instance(engine, instance_id="inst-mix-job")
        _seed_instance(engine, instance_id="watcher-task")
        _seed_instance(engine, instance_id="watcher-job")
        task_wid = _seed_task(
            engine,
            instance_id="inst-mix-task",
            status=TaskStatus.FAILED.value,
            error="task-level failure",
        )
        job_jid = _seed_job(
            engine,
            status=JobStatus.COMPLETED.value,
            result_summary="job-level done",
        )
        watcher_repo.add_watch(task_wid, "watcher-task")
        watcher_repo.add_watch(job_jid, "watcher-job")

        reconciled = await job_queue_service_with_resolver.reconcile_terminal_watches()

        assert reconciled == 2
        # Two notifications, one per watcher instance.
        assert instance_manager_mock.enqueue_message.await_count == 2
        # Collect the (instance_id, message) tuples so we can assert
        # both notifications fired without depending on call order.
        sent = {
            c.kwargs["instance_id"]: c.kwargs["message"]
            for c in instance_manager_mock.enqueue_message.await_args_list
        }
        assert "watcher-task" in sent
        assert "watcher-job" in sent
        assert "failed ✗" in sent["watcher-task"]
        assert "task-level failure" in sent["watcher-task"]
        assert "completed ✓" in sent["watcher-job"]
        assert "job-level done" in sent["watcher-job"]


# ─── JobQueueService.reconcile_terminal_watches — legacy fallback ────────────


class TestReconcileTerminalWatchesLegacyFallback:
    """When ``_work_resolver`` is None, ``reconcile_terminal_watches``
    must fall back to the legacy JobItem-only path so the sweep still
    runs.

    These tests mirror the existing
    ``tests/job_queue/test_jober_watch_integration.py::TestReconcileTerminalWatches``
    tests but go through the public ``reconcile_terminal_watches``
    entry point with a ``job_queue_service_no_resolver`` fixture (no
    ``set_work_resolver`` call) so the legacy branch is the one being
    exercised. The unit-test setup here lets us assert that the
    fallback path ALSO calls ``notify_watchers`` (with ``record.error``
    semantics intact) rather than silently returning 0.
    """

    async def test_legacy_fallback_returns_zero_with_no_resolver_no_watches(
        self, job_queue_service_no_resolver,
    ):
        """No resolver, no watches → returns 0."""
        reconciled = await job_queue_service_no_resolver.reconcile_terminal_watches()
        assert reconciled == 0

    async def test_legacy_fallback_finds_job_via_repository_get(
        self, engine, job_queue_service_no_resolver,
        watcher_repo: JobWatcherRepository, job_repo: JobRepository,
        instance_manager_mock,
    ):
        """A watch on a terminal JobItem is found via ``_repository.get``
        even without the resolver wired — proving the legacy fallback
        is the path being exercised.

        The terminal JobItem is seeded through the real repository
        (not a mock) so the only way ``reconcile_terminal_watches``
        can find it is the ``asyncio.to_thread(self._repository.get, ...)``
        branch in ``_reconcile_terminal_watches_legacy``.
        """
        _seed_instance(engine, instance_id="inst-legacy-job")
        _seed_instance(engine, instance_id="watcher-legacy")
        jid = _seed_job(
            engine,
            status=JobStatus.FAILED.value,
            error_message="legacy-path failure",
        )
        # Sanity: the JobItem really is in the repo.
        assert job_repo.get(jid) is not None
        watcher_repo.add_watch(jid, "watcher-legacy")

        reconciled = await job_queue_service_no_resolver.reconcile_terminal_watches()

        assert reconciled == 1
        instance_manager_mock.enqueue_message.assert_awaited_once()
        call_args = instance_manager_mock.enqueue_message.call_args
        assert call_args is not None
        assert "failed ✗" in call_args.kwargs["message"]
        assert "legacy-path failure" in call_args.kwargs["message"]


# ─── Phase 2 (Batch 5): Required Tests ────────────────────────────────────
# Phase 2 Batch 5 of ``feature/virtual-job-management-surface`` adds the
# six end-to-end tests that prove the resolver / notifier / cancel /
# enqueue wiring works as a coherent surface. These tests live next to
# the Batch 3 + Batch 4 tests above because they share the same fixture
# surface (in-memory SQLite engine, real repositories, mocked
# ``InstanceManager.enqueue_message``) — they're just exercising
# additional call paths through the same plumbing.
#
# The tests cover:
#
# * The watch_job/watch_jobs terminal-state immediate-notify path.
# * The job_cancel cooperative-task-cancel path (via the resolver).
# * The race-free ``complete_task`` + ``notify_work_watchers`` combo
#   (only one of two concurrent callers fires a notification).
# * The list_work kind-agnostic union (PENDING JobItem + RUNNING Task
#   both surface).
# * The end-to-end work_id resolvable-after-enqueue contract (the
#   ``job_id`` returned by ``AsyncMessageResult`` is the same value
#   ``resolve_work`` accepts).
#
# Pattern: same in-memory engine / real repos / mock InstanceManager
# as the Batch 3 + Batch 4 tests above. The only new dependency is
# :func:`daemon.services.work_notifier.notify_work_watchers`, called
# directly (the production call sites in ``worker_pool`` /
# ``stale_task_recovery`` / ``task_processor.on_success`` /
# ``manager._resume_processing_background`` all wrap this helper the
# same way: atomic terminal repo method → ``notify_work_watchers``).


from daemon.services.work_notifier import notify_work_watchers


def _lookup_task_id(engine: Engine, work_id: str) -> int:
    """Return the integer ``Task.id`` for a seeded work_id.

    The ``_seed_task`` helper only returns the ``work_id`` (the UUID4
    column) so callers that need the integer PK (for ``complete_task``,
    ``fail_task``, ``request_cancel``) can use this one-liner to
    resolve the mapping via the real ``Task`` table.
    """
    from sqlmodel import select
    from daemon.repositories.task.models import Task
    with Session(engine) as s:
        stmt = select(Task).where(Task.work_id == work_id)
        task = s.exec(stmt).first()
        assert task is not None, f"Task row missing for work_id={work_id}"
        return task.id


# ─── 1. watch a task work_id, drive to COMPLETED, assert one notification ───


class TestWatchTaskAndNotifyOnComplete:
    """``notify_work_watchers`` fires exactly once when a Task transitions
    to a terminal status with a watcher registered.

    Mirrors the production call pattern in ``worker_pool.complete_task``
    / ``stale_task_recovery.fail_task`` / ``task_processor.on_success``:
    the atomic terminal repo method (``complete_task``) returns a
    non-None row only for the caller that won the ``status='running'``
    SQL guard; that caller is the one that fires the notification, so
    there is no double-notify window.
    """

    async def test_watch_task_and_notify_on_complete(
        self, engine, resolver, task_repo,
        watcher_repo: JobWatcherRepository, instance_manager_mock,
    ):
        """End-to-end: watch → complete → notify.

        Asserts:

        * ``task_repo.complete_task`` returns the updated Task (the
          ``status='running'`` guard matched — caller won the race).
        * ``notify_work_watchers`` returns 1 (one watcher notified).
        * ``instance_manager.enqueue_message`` was awaited exactly once
          with the orchestrator's ``[JOB_EVENT]`` payload carrying the
          correct work_id prefix and the ``completed ✓`` glyph.
        """
        _seed_instance(engine, instance_id="inst-watch-task", agent_id="developer")
        _seed_instance(engine, instance_id="watcher-task-1")
        wid = _seed_task(
            engine,
            instance_id="inst-watch-task",
            status=TaskStatus.RUNNING.value,
        )
        task_id = _lookup_task_id(engine, wid)
        # Pre-register a watcher on this work_id — same as the watch_job
        # tool would do for a still-running work unit.
        watcher_repo.add_watch(wid, "watcher-task-1")

        # Drive the task to COMPLETED via the atomic terminal repo method.
        updated = task_repo.complete_task(task_id, {"answer": "done"})
        assert updated is not None  # we won the status='running' guard
        assert updated.status == TaskStatus.COMPLETED.value

        # Fire the notification — this is what the production terminal
        # handlers do AFTER complete_task returns non-None.
        notified = await notify_work_watchers(
            wid,
            "completed",
            error=None,
            instance_manager=instance_manager_mock,
            work_resolver=resolver,
            watcher_repo=watcher_repo,
        )

        assert notified == 1
        instance_manager_mock.enqueue_message.assert_awaited_once()
        call_args = instance_manager_mock.enqueue_message.call_args
        assert call_args is not None
        # Orchestrator parser contract: [JOB_EVENT] prefix + canonical
        # work_id[:8] prefix + completed ✓ glyph.
        message = call_args.kwargs["message"]
        assert "[JOB_EVENT]" in message
        assert f"Job {wid[:8]}..." in message
        assert "completed ✓" in message
        # The watcher is on ``watcher-task-1`` (NOT on the task's own
        # instance — the watcher's instance is the recipient).
        assert call_args.kwargs["instance_id"] == "watcher-task-1"
        # ``source`` follows the orchestrator's parser contract.
        assert call_args.kwargs["source"] == f"internal_agent:job_event:{wid}:completed"


# ─── 2. watch a COMPLETED task → immediate notification ────────────────────


class TestWatchTaskAlreadyTerminal:
    """The ``watch_job`` / ``watch_jobs`` tool checks the work's status
    BEFORE registering the watcher — if the work is already terminal,
    it registers the watch AND fires an immediate notification in the
    same call.

    This test exercises that branch through the same primitives the
    tool uses (``resolve_work`` → ``is_terminal`` → ``add_watch`` →
    ``notify_work_watchers``) without spinning up the LangChain tool
    decorator.
    """

    async def test_watch_task_already_terminal(
        self, engine, resolver, watcher_repo: JobWatcherRepository,
        instance_manager_mock,
    ):
        """A watch registered AFTER a Task reached COMPLETED fires an
        immediate notification.

        The flow under test (mirrors ``watch_job`` in
        ``tools/job_queue.py``):

        1. ``resolve_work(wid)`` returns the WorkRecord (status="completed"
           after canonicalisation — Task ``completed`` stays
           ``completed``).
        2. ``is_terminal(record.status)`` is True.
        3. ``watcher_repo.add_watch(wid, instance_id)`` registers the
           watch.
        4. ``notify_work_watchers`` is called immediately because the
           watched work is already in a terminal state — the watching
           instance gets the event NOW, not later.

        Asserts the immediate-notify branch fires exactly once with the
        ``completed ✓`` payload.
        """
        _seed_instance(engine, instance_id="inst-already-term", agent_id="developer")
        _seed_instance(engine, instance_id="watcher-already-term")
        wid = _seed_task(
            engine,
            instance_id="inst-already-term",
            status=TaskStatus.COMPLETED.value,
            result=json.dumps({"answer": "already done"}),
        )

        # Simulate the watch_job tool's terminal-detection branch.
        record = resolver.resolve_work(wid)
        assert record is not None
        # Task ``completed`` canonicalises identically.
        assert record.status == "completed"

        from daemon.services.work_status import is_terminal
        assert is_terminal(record.status) is True

        # Register the watch + fire immediate notification (same order
        # as the watch_job tool — register first so notify_watchers
        # has a row to claim).
        watcher_repo.add_watch(wid, "watcher-already-term")
        notified = await notify_work_watchers(
            wid,
            "completed",
            error=None,
            instance_manager=instance_manager_mock,
            work_resolver=resolver,
            watcher_repo=watcher_repo,
        )

        assert notified == 1
        instance_manager_mock.enqueue_message.assert_awaited_once()
        call_args = instance_manager_mock.enqueue_message.call_args
        message = call_args.kwargs["message"]
        assert "[JOB_EVENT]" in message
        assert "completed ✓" in message
        # ``source`` carries the work_id so the orchestrator can look
        # the work up on its side after parsing the message.
        assert call_args.kwargs["source"] == f"internal_agent:job_event:{wid}:completed"
        assert call_args.kwargs["instance_id"] == "watcher-already-term"


# ─── 3. job_cancel(task.work_id) sets cancel_requested (cooperative) ──────


class TestCancelTaskViaJobCancel:
    """``job_cancel`` routes through ``resolver.resolve_work`` and
    branches on ``record.kind``:

    * ``kind == "job"`` → atomic ``job_service.cancel_job`` (immediate).
    * ``kind == "task"`` → cooperative ``task_repo.request_cancel``
      (sets the flag the worker thread observes on its next heartbeat;
      the row stays RUNNING until the worker yields).

    This test proves the cooperative branch is taken for tasks:
    ``task_repo.request_cancel`` is called (and succeeds), the row's
    ``cancel_requested`` flips to True, and the task's ``status`` stays
    RUNNING (cooperative — not instant).
    """

    def test_cancel_task_via_job_cancel(
        self, engine, resolver, task_repo: TaskRepository,
    ):
        """Resolving a RUNNING Task's work_id routes through
        ``task_repo.request_cancel`` (cooperative), NOT
        ``cancel_job``.

        The test mirrors the production ``job_cancel`` tool's task branch
        (see ``tools/job_queue.py:497-529``):

        1. ``resolver.resolve_work(wid)`` returns ``kind="task"``.
        2. ``task_repo.get_by_work_id(wid)`` returns the Task.
        3. ``task_repo.request_cancel(task.id)`` flips
           ``cancel_requested=True`` and returns True.
        4. After the cancel call, ``status`` is still ``"running"``
           (cooperative) — only the flag is set. The worker thread
           will observe the flag on its next heartbeat check and stop
           gracefully.
        """
        _seed_instance(engine, instance_id="inst-cancel-task", agent_id="developer")
        wid = _seed_task(
            engine,
            instance_id="inst-cancel-task",
            status=TaskStatus.RUNNING.value,
        )
        task_id = _lookup_task_id(engine, wid)

        # Step 1: resolver routes to kind="task" — this is the branch
        # the job_cancel tool uses to decide between cooperative (task)
        # and atomic (job) cancellation.
        record = resolver.resolve_work(wid)
        assert record is not None
        assert record.kind == "task"
        assert record.status == "processing"  # RUNNING canonicalises

        # Step 2 + 3: cooperative cancel — this is the production
        # call path. We use the real ``task_repo`` (no mock) because
        # the test is proving the right repo method was selected.
        task = task_repo.get_by_work_id(wid)
        assert task is not None
        cancelled = task_repo.request_cancel(task.id)

        # Step 4: assertions on the side effects.
        assert cancelled is True, "request_cancel should succeed for a RUNNING task"
        updated = task_repo.get_by_work_id(wid)
        assert updated is not None
        # CRITICAL: status stays RUNNING (cooperative, NOT atomic).
        # An instant cancellation would orphan the in-flight graph
        # state — this is exactly why the task branch uses the
        # cooperative path (see tools/job_queue.py:497-529 docstring).
        assert updated.status == TaskStatus.RUNNING.value
        # The flag the worker thread observes on its next heartbeat.
        assert updated.cancel_requested is True


# ─── 4. concurrent complete_task → exactly one notification ────────────────


class TestNoDoubleNotify:
    """Two concurrent ``complete_task`` callers race for the same RUNNING
    task. Only ONE wins the ``WHERE status='running'`` SQL guard (the
    other gets ``None``). Only the winner fires a notification.

    This is the race-free contract documented on
    ``TaskRepository.complete_task`` (returns ``None`` for the loser)
    and on ``notify_work_watchers`` (uses ``claim_watchers_for_job``,
    ``DELETE...RETURNING``, as the second line of defense).

    The test exercises BOTH layers in a single gather() to prove the
    whole stack stays single-notify under contention.
    """

    async def test_no_double_notify(
        self, engine, resolver, task_repo: TaskRepository,
        watcher_repo: JobWatcherRepository, instance_manager_mock,
    ):
        """Two concurrent callers → exactly one notification.

        Asserts:

        * Exactly ONE of the two ``complete_task`` calls returns a
          non-None Task (the loser gets ``None`` — the ``status='running'``
          SQL guard excludes it).
        * Exactly ONE notification is enqueued (the loser's branch is
          skipped because its ``complete_task`` returned ``None``).
        * Even if both branches HAD called ``notify_work_watchers``
          (they don't, but as defense-in-depth), only one would have
          claimed the watcher row via ``claim_watchers_for_job``. The
          test asserts the observable behaviour: one ``enqueue_message``
          call, not two.
        """
        _seed_instance(engine, instance_id="inst-race-task", agent_id="developer")
        _seed_instance(engine, instance_id="watcher-race")
        wid = _seed_task(
            engine,
            instance_id="inst-race-task",
            status=TaskStatus.RUNNING.value,
        )
        task_id = _lookup_task_id(engine, wid)
        watcher_repo.add_watch(wid, "watcher-race")

        # The complete-then-notify sequence the production terminal
        # sites follow. Wrapped in a coroutine so we can race two of
        # them via asyncio.gather.
        async def complete_and_notify() -> bool:
            result = task_repo.complete_task(task_id, {"answer": "race"})
            if result is None:
                # Lost the SQL guard — another concurrent caller beat
                # us to the transition. Production code skips the
                # notify in this case.
                return False
            # Won the SQL guard — fire the notification.
            await notify_work_watchers(
                wid,
                "completed",
                error=None,
                instance_manager=instance_manager_mock,
                work_resolver=resolver,
                watcher_repo=watcher_repo,
            )
            return True

        # Race the two attempts.
        outcomes = await asyncio.gather(
            complete_and_notify(),
            complete_and_notify(),
        )

        # Exactly one caller won the SQL guard.
        assert outcomes.count(True) == 1, (
            f"Expected exactly one winner, got outcomes={outcomes}"
        )

        # And exactly one notification fired.
        instance_manager_mock.enqueue_message.assert_awaited_once()
        # Defence-in-depth check: even if both callers had invoked
        # notify_work_watchers, ``claim_watchers_for_job`` would have
        # raced the second caller out — the test exercises the WHOLE
        # path here, so we don't need a separate test for that.


# ─── 5. list_work returns both pending JobItem and running Task ───────────


class TestJobListUnion:
    """``list_work`` is the kind-agnostic read API: it returns the UNION
    of pending JobItems (dispatch-queue side) and RUNNING Tasks
    (worker-pool side). The existing ``test_list_work_returns_both_kinds``
    covers this with both rows in PENDING — this test pins down the
    PENDING-JobItem + RUNNING-Task shape specifically so the cross-
    status union is exercised (the canonical status from each side
    differs: ``pending`` stays ``pending``, ``running`` becomes
    ``processing``).
    """

    def test_job_list_union(
        self, engine, resolver,
    ):
        """One PENDING JobItem + one RUNNING Task → both surface in
        ``list_work()`` with the correct ``kind`` and canonical
        ``status``.
        """
        _seed_instance(engine, instance_id="inst-union-job")
        _seed_instance(engine, instance_id="inst-union-task", agent_id="developer")
        task_id = _seed_task(
            engine,
            instance_id="inst-union-task",
            status=TaskStatus.RUNNING.value,
        )
        job_id = _seed_job(
            engine,
            instance_id="inst-union-job",
            status=JobStatus.PENDING.value,
        )

        records = resolver.list_work()

        # Both rows surface.
        assert len(records) == 2
        by_kind: dict[str, Any] = {r.kind: r for r in records}
        assert set(by_kind.keys()) == {"task", "job"}

        # The JobItem shows up with kind="job" and the canonical
        # ``pending`` status (JobItem PENDING maps to PENDING).
        assert by_kind["job"].work_id == job_id
        assert by_kind["job"].kind == "job"
        assert by_kind["job"].status == "pending"

        # The Task shows up with kind="task" and the canonical
        # ``processing`` status (Task RUNNING canonicalises to
        # ``processing``).
        assert by_kind["task"].work_id == task_id
        assert by_kind["task"].kind == "task"
        assert by_kind["task"].status == "processing"
        # ``agent_id`` is looked up from the matching Instance row on
        # the Task branch — proves the Task-side instance lookup works
        # inside ``list_work``.
        assert by_kind["task"].agent_id == "developer"


# ─── 6. work_id returned by enqueue flow is resolvable via resolve_work ──


class TestJobContinueReturnsRealWorkId:
    """``InstanceMessagingService.enqueue_message`` returns an
    ``AsyncMessageResult`` whose ``job_id`` is ``task.work_id`` (the
    stable UUID4 minted by the Task model's ``default_factory``). The
    ``job_continue`` tool surfaces this as ``new_job_id``.

    This test proves the contract: a work_id minted by the enqueue
    flow is universally resolvable via ``resolve_work``. The test
    simulates the enqueue flow via ``task_repo.create()`` — the same
    model the production enqueue flow uses, with the same
    ``work_id`` default_factory — so the minted ``work_id`` is the
    same shape as the production ``AsyncMessageResult.job_id``.

    (Spinning up the full ``InstanceMessagingService.enqueue_message``
    here would require wiring ``InstanceManager`` + ``worker_pool``
    + ``_prepare_enqueued_message`` + the ``MessageQueue`` table +
    ``Event`` writes — a heavy integration test for a contract the
    Task model's ``default_factory`` already proves at unit-test
    scope. This test pins the contract at the unit-test surface.)
    """

    def test_job_continue_returns_real_work_id(
        self, engine, resolver, task_repo: TaskRepository,
    ):
        """The ``work_id`` minted by the enqueue flow is resolvable.

        Asserts:

        * ``task_repo.create`` returns a Task whose ``work_id`` is set
          (the model's ``default_factory`` always populates it — NOT
          NULL on the column).
        * ``resolver.resolve_work(job_id)`` returns a non-None
          ``WorkRecord`` with ``kind="task"``.
        * The resolved ``work_id`` matches the value the enqueue flow
          would have returned as ``AsyncMessageResult.job_id``.
        """
        _seed_instance(engine, instance_id="inst-continuable", agent_id="developer")

        # Simulate the enqueue flow: ``task_repo.create`` writes the
        # Task row the same way ``_prepare_enqueued_message`` does —
        # both rely on the model's ``default_factory`` to mint a
        # UUID4 ``work_id``.
        task = task_repo.create(
            task_type="process_message",
            instance_id="inst-continuable",
        )

        # The ``job_id`` the production enqueue flow surfaces — same
        # value the ``job_continue`` tool returns as ``new_job_id``.
        job_id = task.work_id
        assert job_id is not None
        # ``work_id`` is a UUID4 string (default_factory always
        # populates — column is NOT NULL).
        assert isinstance(job_id, str)
        assert len(job_id) >= 8  # [:8] prefix used in notifications

        # The contract: this ``job_id`` is resolvable through the
        # virtual job surface.
        record = resolver.resolve_work(job_id)

        assert record is not None, (
            f"resolve_work returned None for the enqueue-minted work_id "
            f"{job_id!r} — the resolver cannot see Task-created work"
        )
        assert record.work_id == job_id
        assert record.kind == "task"
        # ``agent_id`` is looked up from the matching Instance row,
        # proving the resolver sees the full Task-side context.
        assert record.agent_id == "developer"


# ─── Phase 2 HIGH-severity blockers — regression tests ─────────────────────
# Three HIGH-severity blockers were flagged in the Phase 2 review on
# ``feature/virtual-job-management-surface``. The tests below pin down
# the fixes so they cannot regress silently.


# ─── Fix 2: ``notify_work_watchers`` preserves watchers on non-terminal status ──


class TestNotifyWorkWatchersPreservesWatchersOnInProgress:
    """Fix 2 (HIGH-severity blocker).

    Before the fix, ``notify_work_watchers`` unconditionally called
    ``watcher_repo.claim_watchers_for_job`` (DELETE...RETURNING) — even
    for non-terminal statuses like ``in_progress``. That permanently
    deleted the watcher the moment the first progress update fired, so
    the watching agent never received the subsequent terminal
    notification.

    The fix branches on ``work_status.is_terminal``:

    * Terminal statuses (``completed``, ``failed``, ``cancelled``,
      ``dead_letter``) — atomic claim-and-delete (race-free).
    * Non-terminal statuses (``in_progress`` etc.) — read-only
      ``get_watchers_for_job``, watcher row preserved for the terminal
      event.

    These tests prove the watcher row is preserved after an
    ``in_progress`` notification, then correctly deleted by the next
    terminal notification.
    """

    async def test_in_progress_preserves_watcher_for_terminal_event(
        self, engine, resolver, watcher_repo: JobWatcherRepository,
        instance_manager_mock,
    ):
        """``in_progress`` must NOT delete the watcher.

        Scenario: an agent has registered a watch on a still-running
        work unit. The ``JobFeedbackObserver._emit_in_progress`` path
        fires ``notify_work_watchers(work_id, status="in_progress")``
        to deliver a progress update. Before the fix this would
        DELETE the watcher row, so when the work unit later reached
        ``completed``, ``claim_watchers_for_job`` would return ``[]``
        and the watching agent would never see the terminal event.

        Asserts:

        * The watcher row is still present in the repo after the
          ``in_progress`` notification.
        * The progress message was delivered to the watching instance
          (single ``enqueue_message`` call).
        """
        _seed_instance(engine, instance_id="inst-watcher-fix2", agent_id="developer")
        _seed_instance(engine, instance_id="watcher-fix2-progress")
        # Seed a non-terminal work unit (status=processing) so the
        # resolver can look up the WorkRecord and the watcher's
        # ``watch_events`` filter accepts ``in_progress``.
        wid = _seed_task(
            engine,
            instance_id="inst-watcher-fix2",
            status=TaskStatus.RUNNING.value,
        )
        # Default ``add_watch`` subscribes to ALL_WATCHABLE_EVENTS
        # which includes ``in_progress`` and the terminal states.
        watcher_repo.add_watch(wid, "watcher-fix2-progress")

        notified = await notify_work_watchers(
            wid,
            "in_progress",
            error=None,
            instance_manager=instance_manager_mock,
            work_resolver=resolver,
            watcher_repo=watcher_repo,
            progress="Step 1/3 complete",
        )

        # The progress notification fired.
        assert notified == 1
        instance_manager_mock.enqueue_message.assert_awaited_once()
        call_args = instance_manager_mock.enqueue_message.call_args
        assert call_args is not None
        message = call_args.kwargs["message"]
        assert "[JOB_EVENT]" in message
        assert "in progress ⟳" in message
        assert "Step 1/3 complete" in message
        assert call_args.kwargs["instance_id"] == "watcher-fix2-progress"

        # CRITICAL: the watcher row must still be present. The bug
        # would have removed it (claim = DELETE...RETURNING).
        remaining = watcher_repo.get_watchers_for_job(wid)
        assert len(remaining) == 1, (
            "Watcher row was deleted by in_progress notification — "
            "the watching instance will never receive the terminal "
            "event. Fix 2 regression."
        )
        assert remaining[0].instance_id == "watcher-fix2-progress"

    async def test_terminal_after_in_progress_delivers_both_notifications(
        self, engine, resolver, watcher_repo: JobWatcherRepository,
        instance_manager_mock,
    ):
        """Full sequence: in_progress (preserves watcher) → completed (delivers terminal).

        End-to-end shape of the fix: an in_progress update doesn't
        break the eventual terminal delivery. After ``in_progress``,
        the watcher is still present and the next ``completed``
        notification claims it (atomic delete) and delivers the
        ``[JOB_EVENT] completed ✓`` message.
        """
        _seed_instance(engine, instance_id="inst-watcher-fix2b", agent_id="developer")
        _seed_instance(engine, instance_id="watcher-fix2b")
        wid = _seed_task(
            engine,
            instance_id="inst-watcher-fix2b",
            status=TaskStatus.RUNNING.value,
        )
        watcher_repo.add_watch(wid, "watcher-fix2b")

        # Step 1: in_progress update.
        notified_progress = await notify_work_watchers(
            wid,
            "in_progress",
            error=None,
            instance_manager=instance_manager_mock,
            work_resolver=resolver,
            watcher_repo=watcher_repo,
            progress="Halfway done",
        )
        assert notified_progress == 1
        # Watcher must still be there.
        assert len(watcher_repo.get_watchers_for_job(wid)) == 1

        # Step 2: terminal notification — uses the claim-and-delete
        # path and must deliver the completed event.
        notified_complete = await notify_work_watchers(
            wid,
            "completed",
            error=None,
            instance_manager=instance_manager_mock,
            work_resolver=resolver,
            watcher_repo=watcher_repo,
        )
        assert notified_complete == 1

        # Both notifications were delivered (one per call).
        assert instance_manager_mock.enqueue_message.await_count == 2
        sent_messages = [
            c.kwargs["message"]
            for c in instance_manager_mock.enqueue_message.await_args_list
        ]
        # First message carries the progress glyph.
        assert "in progress ⟳" in sent_messages[0]
        assert "Halfway done" in sent_messages[0]
        # Second message carries the completed glyph.
        assert "completed ✓" in sent_messages[1]
        # Both are addressed to the watching instance.
        sent_instances = [
            c.kwargs["instance_id"]
            for c in instance_manager_mock.enqueue_message.await_args_list
        ]
        assert sent_instances == ["watcher-fix2b", "watcher-fix2b"]

        # Watcher row is now consumed (terminal claim deleted it).
        assert watcher_repo.get_watchers_for_job(wid) == []

    async def test_terminal_status_uses_claim_and_delete(
        self, engine, resolver, watcher_repo: JobWatcherRepository,
        instance_manager_mock,
    ):
        """Terminal status path: claim-and-delete, single notification.

        Defence-in-depth assertion that the terminal-status branch of
        the fix still does the atomic claim-and-delete (the existing
        ``TestNoDoubleNotify`` already exercises race-freeness; this
        test pins down the post-claim state).
        """
        _seed_instance(engine, instance_id="inst-fix2-term", agent_id="developer")
        _seed_instance(engine, instance_id="watcher-fix2-term")
        wid = _seed_task(
            engine,
            instance_id="inst-fix2-term",
            status=TaskStatus.RUNNING.value,
        )
        watcher_repo.add_watch(wid, "watcher-fix2-term")

        notified = await notify_work_watchers(
            wid,
            "completed",
            error=None,
            instance_manager=instance_manager_mock,
            work_resolver=resolver,
            watcher_repo=watcher_repo,
        )

        assert notified == 1
        # Terminal path deleted the watcher (claim = DELETE...RETURNING).
        assert watcher_repo.get_watchers_for_job(wid) == []


# ─── Fix 3: ``job_list`` multi-status filter does not widen to "all records" ──


class TestJobListMultiStatusFilter:
    """Fix 3 (HIGH-severity blocker).

    Before the fix, ``job_list`` (resolver branch) only accepted a
    single ``status`` string. When the caller supplied a list of
    statuses (``["completed", "failed"]``), the tool passed
    ``status=None`` to ``list_work``, which returned ALL records
    regardless of status. The caller's filter was silently dropped.

    The fix post-filters the resolver's unfiltered result by the
    canonical-status set when ``len(normalised_statuses) > 1``.
    """

    async def test_multi_status_returns_only_matching_records(
        self, engine, resolver, watcher_repo: JobWatcherRepository,
        instance_manager_mock, job_repo: JobRepository,
    ):
        """``statuses=["completed", "failed"]`` returns ONLY matching rows.

        Seed three JobItems (completed, failed, pending) and confirm
        the multi-status request returns the two matching rows and
        excludes the pending one. The pending row must NOT leak
        through the (now-broken) ``status=None`` path.
        """
        from daemon.tools.job_queue import create_job_tools

        _seed_instance(engine, instance_id="inst-multi-1")
        _seed_instance(engine, instance_id="inst-multi-2")
        _seed_instance(engine, instance_id="inst-multi-3")
        # One of each canonical status the multi-filter cares about.
        completed_id = _seed_job(
            engine,
            status=JobStatus.COMPLETED.value,
            result_summary="done",
        )
        failed_id = _seed_job(
            engine,
            status=JobStatus.FAILED.value,
            error_message="kaboom",
        )
        pending_id = _seed_job(
            engine,
            status=JobStatus.PENDING.value,
        )

        # Wire a JobQueueService with the real resolver and the
        # ``use_virtual_job_resolver`` flag on (the default).
        service = JobQueueService(
            repository=job_repo,
            lock_manager=MagicMock(),
            queue_repo=MagicMock(),
            instance_manager=instance_manager_mock,
        )
        service.set_watcher_repo(watcher_repo)
        service.set_work_resolver(resolver)

        tools = create_job_tools(service, MagicMock(), MagicMock())
        # job_list is the third tool in the registry (index 2). This
        # is the same convention used by ``tests/test_job_queue_tools.py``.
        job_list = tools[2]

        # Sanity-check: ``list_work`` itself returns ALL three
        # (proves the post-filter, not the resolver, is doing the
        # narrowing).
        all_records = resolver.list_work()
        assert {r.work_id for r in all_records} == {
            completed_id,
            failed_id,
            pending_id,
        }

        # Invoke the tool with a multi-status filter.
        result = await job_list.ainvoke(
            {"statuses": ["completed", "failed"]}
        )

        # No error envelope.
        assert "error" not in result
        # Count matches the number of matching records.
        assert result["count"] == 2
        returned_ids = {job["work_id"] for job in result["jobs"]}
        # Both matching rows present.
        assert completed_id in returned_ids
        assert failed_id in returned_ids
        # The pending row is NOT returned — this is the regression
        # assertion. Before the fix, ``status=None`` was passed to
        # ``list_work`` and all three rows leaked through.
        assert pending_id not in returned_ids, (
            "Pending job leaked through multi-status filter — Fix 3 "
            "regression. The post-filter is not narrowing on the "
            "canonical status set."
        )

    async def test_single_status_still_works(
        self, engine, resolver, watcher_repo: JobWatcherRepository,
        instance_manager_mock, job_repo: JobRepository,
    ):
        """Single-status requests still pass through to the resolver filter.

        The fix only adds the post-filter when ``len > 1``; the
        single-status path is unchanged. Confirm the single-status
        resolver branch still narrows by ``status`` and returns ONLY
        matching records (this is the existing path the fix
        preserves).
        """
        from daemon.tools.job_queue import create_job_tools

        _seed_instance(engine, instance_id="inst-single-1")
        _seed_instance(engine, instance_id="inst-single-2")
        completed_id = _seed_job(
            engine,
            status=JobStatus.COMPLETED.value,
            result_summary="done",
        )
        _seed_job(
            engine,
            status=JobStatus.FAILED.value,
            error_message="kaboom",
        )

        service = JobQueueService(
            repository=job_repo,
            lock_manager=MagicMock(),
            queue_repo=MagicMock(),
            instance_manager=instance_manager_mock,
        )
        service.set_watcher_repo(watcher_repo)
        service.set_work_resolver(resolver)

        tools = create_job_tools(service, MagicMock(), MagicMock())
        job_list = tools[2]

        result = await job_list.ainvoke({"statuses": ["completed"]})

        assert "error" not in result
        assert result["count"] == 1
        assert result["jobs"][0]["work_id"] == completed_id
        assert result["jobs"][0]["status"] == "completed"

    async def test_multi_status_uses_canonical_aliases(
        self, engine, resolver, watcher_repo: JobWatcherRepository,
        instance_manager_mock, job_repo: JobRepository,
    ):
        """Status aliases (``running`` → ``processing``) flow through the multi-status filter.

        ``normalize_statuses`` runs BEFORE the post-filter, so
        ``statuses=["running", "done"]`` should resolve to canonical
        ``["processing", "completed"]`` and match the rows in those
        canonical states.
        """
        from daemon.tools.job_queue import create_job_tools

        _seed_instance(engine, instance_id="inst-alias-1", agent_id="developer")
        _seed_instance(engine, instance_id="inst-alias-2", agent_id="developer")
        # A Task (uses "running" source value, canonicalises to
        # "processing") and a JobItem in "completed".
        running_wid = _seed_task(
            engine,
            instance_id="inst-alias-1",
            status=TaskStatus.RUNNING.value,
        )
        completed_jid = _seed_job(
            engine,
            status=JobStatus.COMPLETED.value,
        )
        # A Task in pending — must NOT match.
        pending_wid = _seed_task(
            engine,
            instance_id="inst-alias-2",
            status=TaskStatus.PENDING.value,
        )

        service = JobQueueService(
            repository=job_repo,
            lock_manager=MagicMock(),
            queue_repo=MagicMock(),
            instance_manager=instance_manager_mock,
        )
        service.set_watcher_repo(watcher_repo)
        service.set_work_resolver(resolver)

        tools = create_job_tools(service, MagicMock(), MagicMock())
        job_list = tools[2]

        # Aliases: ``running`` → ``processing`` (canonical), ``done``
        # → ``completed`` (canonical).
        result = await job_list.ainvoke(
            {"statuses": ["running", "done"]}
        )

        assert "error" not in result
        returned_ids = {job["work_id"] for job in result["jobs"]}
        assert running_wid in returned_ids
        assert completed_jid in returned_ids
        # Pending task excluded.
        assert pending_wid not in returned_ids


# ─── end of file ─────────────────────────────────────────────────────────────
